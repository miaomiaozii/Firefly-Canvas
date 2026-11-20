"""Agent canvas tools API endpoints.

These endpoints are called by the Pi agent's canvas-tools extension via localhost HTTP.
They execute canvas mutations through the existing project store.
"""
import asyncio
import contextvars
import mimetypes
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
import json
import sqlite3
import uuid
import weakref
from typing import Callable

from app.services.agent_node_state import ownership_fields, project_nodes

import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK, ensure_agent_same_origin
from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP
from app.services import agent_frame
from app.services.agent_errors import agent_error
from app.services.agent_tools_manifest import UPDATABLE_NODE_FIELDS as _UPDATABLE_NODE_FIELDS
from app.core import OnlineImageRequest, AIReference
from app.services.misc import build_online_image_result
from firefly_generation import ImageGenerationRequest, ModelRoutingError
from firefly_projects import ProjectStoreError

# #240：agent-tools 系列端点接入同源/Host 守卫（与现有 API 一致；
# 无 Origin 的本机回环 sidecar 调用按票面模型放行）。
router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"], dependencies=[Depends(ensure_agent_same_origin)])

generate_router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["agent-generate"], dependencies=[Depends(ensure_agent_same_origin)])

media_router = APIRouter(prefix="/api/projects/{project_id}/media", tags=["agent-media"], dependencies=[Depends(ensure_agent_same_origin)])

viewport_router = APIRouter(prefix="/api/projects/{project_id}/viewport", tags=["agent-viewport"], dependencies=[Depends(ensure_agent_same_origin)])

confirm_router = APIRouter(prefix="/api/projects/{project_id}/agent", tags=["agent-confirm"], dependencies=[Depends(ensure_agent_same_origin)])

# Global viewport state per project
VIEWPORT_STATE = {}


class _JournalUnsettled(RuntimeError):
    """A guarded operation has not yet proven its canvas write."""


# One listener per store is required to preserve operation order across requests.
_JOURNALS = {}
_JOURNALS_LOCK = threading.Lock()
_BATCH_GENERATION = contextvars.ContextVar("batch_generation", default=None)
_MEDIA_LIFECYCLE = contextvars.ContextVar("media_lifecycle", default=None)
_LIFETIME_TASKS = set()
_LIFETIME_STOPPING = weakref.WeakSet()
_LIFETIME_LOCK = threading.RLock()
_JOURNALED_TOOLS = frozenset({"canvas.create_node", "canvas.update_node", "canvas.delete_node",
                              "canvas.connect", "canvas.disconnect"})


def _track_lifetime_task(task):
    def discard(done):
        with _LIFETIME_LOCK:
            _LIFETIME_TASKS.discard(done)
    with _LIFETIME_LOCK:
        _LIFETIME_TASKS.add(task)
        task.add_done_callback(discard)
        if task.get_loop() in _LIFETIME_STOPPING:
            task.cancel()
    return task


def start_agent_ops_lifetime():
    with _LIFETIME_LOCK:
        _LIFETIME_STOPPING.discard(asyncio.get_running_loop())


async def shutdown_agent_ops_lifetime():
    loop = asyncio.get_running_loop()
    with _LIFETIME_LOCK:
        _LIFETIME_STOPPING.add(loop)
    from app.core import logger
    failures = []
    while True:
        with _LIFETIME_LOCK:
            tasks = [task for task in _LIFETIME_TASKS if task.get_loop() is loop]
        if not tasks:
            break
        for task in tasks:
            task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                failures.append(outcome)
                logger.error('Agent operation shutdown task failed: %s', outcome)
        await asyncio.sleep(0)
    if failures:
        raise BaseExceptionGroup('Agent operation shutdown failed', failures)
    logger.warning('Agent operation shutdown: unresolved cleanup remains guarded and may require recovery')


def set_agent_generation_running(store, project_id: str, op_id: str, task_id: str, running: bool) -> None:
    """Associate a task with an operation under the same lock used by undo.

    Register before releasing the lock that publishes the operation; remove only
    after the final child write has settled. Multiple tasks may share an operation.
    """
    if not all(isinstance(value, str) and value.strip() for value in (project_id, op_id, task_id)):
        raise ValueError("project_id, op_id and task_id must be nonempty strings")
    if type(running) is not bool:
        raise ValueError("running must be a bool")
    from app.services.agent_ops_store import set_active_operation
    with store._lock:
        set_active_operation(store, project_id, op_id, task_id, running)
        tasks = store._agent_generation_tasks
        key = (project_id, op_id)
        if running:
            tasks.setdefault(key, set()).add(task_id)
        else:
            active = tasks.get(key)
            if active is not None:
                active.discard(task_id)
                if not active:
                    tasks.pop(key, None)


def agent_generation_is_running(store, project_id: str, op_id: str) -> bool:
    """Check pending or active generation for undo; safe under store._lock."""
    with store._lock:
        return bool(store._agent_generation_tasks.get((project_id, op_id)))


def _journal_for(store):
    from app.services.agent_ops_journal import AgentOpsJournal
    from firefly_opsdb import OpsDb
    # Callers may already hold the store lock while preparing an effect.
    with store._lock, _JOURNALS_LOCK:
        journal = _JOURNALS.get(store)
        if journal is None:
            journal = AgentOpsJournal(store, OpsDb(store.root))
            _JOURNALS[store] = journal
        return journal


def _apply_journaled(store, project_id, mutations, revision, trusted_agent_bindings, context, deletion=None, persisted=None):
    from app.services.agent_ops_delta import build_delta, touched_node_ids
    from app.services.agent_ops_journal import _conflicts, record_sides

    journal = _journal_for(store)
    store.open_project(project_id)
    with journal.mutation(project_id) as operation:
        before = store.canvas(project_id)
        apply = store.apply_agent_host_mutations if trusted_agent_bindings else store.apply_mutations
        result = apply(project_id, mutations, base_revision=revision)
        delta = build_delta(before, store.canvas(project_id))
        if not delta:
            return result
        old, new = record_sides(delta)
        operation.register({"op_id": context.op_id, "turn_id": context.turn_id,
                            "agent_node_id": context.agent_node_id, "tool": context.tool,
                            "args": context.args, "before": old, "after": new,
                            "touched_node_ids": touched_node_ids(delta),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "irreversible_effects": [], "undone": False}, delta)
        deleted_node = deletion is not None and any(
            change["entity"] == "node" and change["id"] == deletion["node_id"]
            and not change["path"] and change["before"]["present"] and not change["after"]["present"]
            for change in delta
        )
        # Only the replacement callback proves that the changed canvas reached disk;
        # an in-memory snapshot cannot distinguish pre- and post-replace failures.
        def listener(pid, canvas):
            if pid != project_id:
                return
            if deleted_node and not any(str(node.get("id") or "") == deletion["node_id"] for node in canvas.get("nodes", [])):
                deletion["committed"] = True
            if persisted is not None and not _conflicts(canvas, delta):
                persisted.update(updated_at=canvas.get("updated_at"), touched_node_ids=touched_node_ids(delta))
        store.add_persisted_canvas_listener(listener)
    try:
        try:
            store.flush(project_id)
        except Exception as exc:
            # Flush includes persistence listeners; retain the receipt even on post-replace failure.
            status = operation.result()
            raise agent_error(500, "INTERNAL_ERROR", f"Operation {context.op_id} flush failed (journal {status['status']}): {exc}",
                              "画布可能已更改；先检查画布和操作日志，勿盲目重试。") from exc
    finally:
        if listener is not None:
            store.remove_persisted_canvas_listener(listener)
    status = operation.result()
    if status["status"] not in ("settled", "append_failed"):
        raise agent_error(500, "INTERNAL_ERROR", f"Operation {context.op_id} journal {status['status']}: {status['error'] or status['conflicts']}",
                          "画布可能已更改；先检查画布和操作日志，勿盲目重试。")
    return result


def _op_result(result):
    from app.services.agent_ops_context import current_agent_ops_context
    context = current_agent_ops_context()
    if context and context.tool in _JOURNALED_TOOLS and context.agent_node_id:
        store = _app_main.FIREFLY_PROJECT_STORE
        receipts = _operation_receipts(store, context.project_id, {context.op_id})
        if context.op_id in receipts:
            return {**result, "op_id": context.op_id,
                    "touched_node_ids": receipts[context.op_id]}
    return result


def _operation_receipts(store, project_id, op_ids):
    """A canvas replacement is independent of its sidecar receipt."""
    journal = _journal_for(store)
    with journal._lock:
        return {op_id: list(entry['record']['touched_node_ids'])
                for op_id in op_ids
                if (entry := journal._entries.get((project_id, op_id))) is not None
                and entry['status'] in ('settled', 'append_failed')}


def _operation_touched(store, project_id, op_ids):
    receipts = _operation_receipts(store, project_id, op_ids)
    return sorted({node_id for ids in receipts.values() for node_id in ids})


async def _rest_agent_call(request, project_id, tool, args, callback, agent_node_id=""):
    from app.services.agent_ops_context import agent_ops_scope
    if not request.headers.get("X-Agent-Project", ""):
        return await callback()
    agent_node_id = agent_node_id or request.query_params.get("agent_node_id", "")
    if not agent_node_id:
        raise agent_error(400, "INVALID_ARGUMENT", "Agent node identity is required for operation journaling",
                          "请在查询串传入发起操作的 agent_node_id，再重试。")
    from app.services.agent_host import agent_host_manager
    turn = agent_host_manager.get_current_turn_id(project_id, agent_node_id)
    with agent_ops_scope(project_id=project_id, agent_node_id=agent_node_id,
                         turn_id=turn, tool=tool, args=args):
        return await callback()


def _media_context(project_id):
    from app.services.agent_ops_context import current_agent_ops_context
    context = current_agent_ops_context()
    if context:
        if context.project_id != project_id or not context.agent_node_id:
            raise agent_error(400, "INVALID_ARGUMENT", "Matching project and agent identity are required for media journaling")
        return context
    return None


def _indexed_media_exists(store, project_id, record):
    if not isinstance(record, dict):
        return False
    root = (store.root / project_id / "media").resolve()
    path = (root / str(record.get("filename") or "")).resolve()
    return path.is_relative_to(root) and path.is_file()


def _append_effect_preflight(store, project_id, record):
    """Require a settled uncertainty record before starting an irreversible effect."""
    from app.services import agent_ops_store
    from firefly_opsdb import OpsDb

    journal = _journal_for(store)
    db = OpsDb(store.root)
    agent_ops_store.append_operation(db, project_id, record)
    with journal._lock:
        entry = journal._entries.get((project_id, record['op_id']))
        if entry is not None and entry['status'] != 'settled':
            raise RuntimeError(f"effect journal {entry['status']}: {entry['error'] or entry['conflicts']}")
    actual = agent_ops_store.get_operation(db, project_id, record['op_id'])
    expected = dict(record)
    if expected.get('parent_op_id') is None:
        expected.pop('parent_op_id', None)
    if actual != expected:
        raise RuntimeError('effect journal did not settle the expected record')


def _media_root(store, project_id, context, effect, guarded):
    record = {"op_id": context.op_id, "turn_id": context.turn_id,
              "agent_node_id": context.agent_node_id, "tool": context.tool, "args": context.args,
              "before": [], "after": [], "touched_node_ids": [],
              "created_at": datetime.now(timezone.utc).isoformat(),
              "irreversible_effects": [effect], "undone": False}
    with store._lock:
        _append_effect_preflight(store, project_id, record)
        set_agent_generation_running(store, project_id, context.op_id, context.op_id, True)
        guarded.add(context.op_id)


async def _media_start(project_id, context, intended):
    lifecycle = {'op_id': context.op_id, 'intended': intended, 'touched': set(), 'updated_at': None,
                 'unregistered': {}, 'guarded': set(), 'listeners': []}
    token = _MEDIA_LIFECYCLE.set(lifecycle)
    try:
        await _generation_archive_to_completion(_broadcast_canvas_updated(
            project_id, source='agent', op_id=context.op_id, touched_node_ids=intended, phase='start'))
    except BaseException:
        await _media_end(project_id, lifecycle, token)
        raise
    return lifecycle, token


async def _media_end(project_id, lifecycle, token):
    store = _app_main.FIREFLY_PROJECT_STORE
    guarded = lifecycle['guarded']
    needs_recovery = bool(lifecycle['unregistered']) or not _generation_release(
        store, project_id, lifecycle['op_id'], guarded, release=False)
    _MEDIA_LIFECYCLE.reset(token)
    async def send_end():
        await _generation_archive_to_completion(_broadcast_canvas_updated(
            project_id, source='agent', updated_at=lifecycle['updated_at'], op_id=lifecycle['op_id'],
            touched_node_ids=sorted(lifecycle['touched']), phase='end'))
    if not needs_recovery:
        await send_end()
        return
    from app.services.agent_ops_context import create_agent_ops_task
    from app.services.agent_ops_async import run_blocking_to_completion
    from app.core import logger as _logger
    recovery_token = f"media-recovery:{lifecycle['op_id']}"
    is_batch = _BATCH_GENERATION.get() is not None
    set_agent_generation_running(store, project_id, lifecycle['op_id'], recovery_token, True)
    async def recover():
        journal = _journal_for(store)
        def settle():
            with store._lock:
                for op_id, (record, delta) in list(lifecycle['unregistered'].items()):
                    try:
                        _batch_register_reserved(journal, project_id, record, delta)
                    except Exception as exc:
                        # A prior canvas write may already be durable. Keep the undo guard
                        # and retry registration rather than sending an unproven end event.
                        raise _JournalUnsettled(str(exc)) from exc
                    lifecycle['unregistered'].pop(op_id)
                    store._agent_generation_unregistered.discard((project_id, op_id))
                store.flush(project_id)
        while True:
            try:
                await run_blocking_to_completion(settle)
                if not _generation_release(store, project_id, lifecycle['op_id'], guarded, release=False):
                    raise _JournalUnsettled('media journal not settled')
                await send_end()
                for listener in lifecycle['listeners']:
                    store.remove_persisted_canvas_listener(listener)
                if not is_batch:
                    _generation_release(store, project_id, lifecycle['op_id'], guarded)
                set_agent_generation_running(store, project_id, lifecycle['op_id'], recovery_token, False)
                return
            except (OSError, sqlite3.Error, _JournalUnsettled) as exc:
                _logger.warning('media journal retry op=%s: %s', lifecycle['op_id'], exc)
                await asyncio.sleep(1)
    _track_lifetime_task(create_agent_ops_task(recover()))


def _media_partial(context, exc):
    return agent_error(500, "INTERNAL_ERROR", f"Media operation {context.op_id} has a partial-unknown outcome: {exc}",
                       "检查操作日志、媒体和画布后再决定是否重试。", retryable=False,
                       details={"op_id": context.op_id, "status": "partial-unknown"})


def _require_media_journal_support(request=None):
    from app.services.agent_ops_context import current_agent_ops_context
    if current_agent_ops_context() or (request is not None and request.headers.get("X-Agent-Project", "")):
        raise agent_error(503, "SERVER_NOT_READY", "Agent media mutation journaling is unavailable",
                          "媒体操作尚不支持安全记账；请用户在界面中操作。")


def _validate_project(project_id: str):
    """Validate that the project exists and return the store."""
    store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        raise agent_error(500, "SERVER_NOT_READY",
                          "Project store not initialized",
                          "服务端未就绪；请用户重启服务后重试，不要反复调工具。")
    try:
        store.open_project(project_id)
    except Exception as e:
        raise agent_error(404, "PROJECT_NOT_FOUND",
                          f"Project not found: {project_id}",
                          "项目不存在；确认 X-Agent-Project 头的 ID，或先让用户打开项目。")


async def _apply_mutations(project_id: str, mutations: list, *, trusted_agent_bindings: bool = False, deletion=None):
    """Apply fixed mutations (new identities/removals, not stale node updates)."""
    return await _apply_mutations_from_snapshot(
        project_id, lambda _canvas: mutations, trusted_agent_bindings=trusted_agent_bindings, deletion=deletion,
    )


async def _apply_mutations_from_snapshot(
    project_id: str, build_mutations: Callable[[dict], list], *, source: str = "agent",
    trusted_agent_bindings: bool = False, deletion=None,
):
    """Build against one snapshot; announce before applying its CAS mutation."""
    from firefly_projects import RevisionConflict

    store = _app_main.FIREFLY_PROJECT_STORE
    for attempt in range(8):
        try:
            from app.services.agent_ops_context import current_agent_ops_context
            context = current_agent_ops_context()
            if context and context.project_id == project_id and context.agent_node_id and context.tool in _JOURNALED_TOOLS:
                from copy import deepcopy
                from app.services.agent_ops_delta import build_delta, touched_node_ids
                # Preview through the store's own validation and normalization, without
                # mutating the live canvas. CAS rejects a stale preview after the await.
                store.open_project(project_id)
                with store._lock:
                    canvas = store.canvas(project_id)
                    mutations = build_mutations(canvas)
                    if not mutations:
                        return None
                    preview = deepcopy(canvas)
                    for mutation in mutations:
                        store._apply_mutation(preview, deepcopy(mutation),
                                              trusted_agent_bindings=trusted_agent_bindings)
                    intended = touched_node_ids(build_delta(canvas, preview))
                    if not intended:
                        return None
                    revision = int(canvas.get('revision') or 0)
                    updated_at = canvas.get('updated_at') or _app_main.now_ms()
                persisted = {}
                try:
                    await _broadcast_canvas_updated(project_id, source=source, updated_at=updated_at,
                                                    op_id=context.op_id, touched_node_ids=intended, phase='start')
                    try:
                        return _apply_journaled(store, project_id, mutations, revision, trusted_agent_bindings,
                                                context, deletion, persisted)
                    except RevisionConflict as exc:
                        raise agent_error(409, "CANVAS_CONFLICT", "Canvas changed after the operation was announced",
                                          "画布已被其他操作修改；检查最新状态后重试。") from exc
                finally:
                    await _broadcast_canvas_updated(project_id, source=source,
                        updated_at=persisted.get('updated_at') or updated_at, op_id=context.op_id,
                        touched_node_ids=persisted.get('touched_node_ids', []), phase='end')
            canvas = store.canvas(project_id)
            revision = int(canvas.get("revision") or 0)
            mutations = build_mutations(canvas)
            if not mutations:
                return None
            # Only server Python callers can opt in; neither JSON nor source
            # tags confer binding-write authority on generic canvas tools.
            apply = store.apply_agent_host_mutations if trusted_agent_bindings else store.apply_mutations
            result = apply(project_id, mutations, base_revision=revision)
            await _broadcast_canvas_updated(project_id, source=source)
            return result
        except RevisionConflict:
            # Revision conflict: another writer updated the canvas.
            # Rebuild against the latest snapshot (up to 8 attempts).
            if attempt < 7:
                continue
            raise agent_error(409, "CANVAS_CONFLICT",
                              "Canvas mutation failed after retries",
                              "画布并发冲突（已自动重试 8 次）；先调用 canvas.list_nodes 查看最新状态再重新提交。")
        except HTTPException:
            raise  # Keep validation/not-found errors from the latest snapshot.
        except Exception as e:
            raise agent_error(409, "CANVAS_CONFLICT", str(e),
                              "画布写入冲突；先调用 canvas.list_nodes 查看最新状态再重试。") from e


async def _broadcast_canvas_updated(project_id: str, source: str = "", *, updated_at=None, **operation) -> None:
    """Best-effort ``canvas_updated`` broadcast after an agent-tools mutation.

    #375: forwards a `source` tag ("agent") into the broadcast payload so the
    front end snapshots its pre-merge state into the canvas undo stack —
    Ctrl+Z must be able to take an agent mutation back out. Default ("")
    keeps every non-tool broadcast byte-identical (#371 stance).
    """
    manager = getattr(_app_main, "manager", None)
    if manager is None:
        return
    try:
        if updated_at is None:
            canvas = _app_main.FIREFLY_PROJECT_STORE.canvas(project_id)
            updated_at = canvas.get("updated_at")
        await manager.broadcast_canvas_updated(project_id, int(updated_at or _app_main.now_ms()), source=source, **operation)
    except Exception:
        pass  # broadcast is best-effort; never fail the mutation because of it


async def _dispatch_viewport_command(project_id: str, command: dict) -> bool:
    """#243: viewport commands go to ONE owning tab, targeted (not broadcast).

    Resolves the tab's client_id by walking the manager's live connections
    against the project_sessions registry (the registry is keyed by
    id(token), so object-keyed lookup goes through session_project), then
    uses send_personal_message (ADR 0022: 命令单 client 定向下发). Returns
    False when the tab is not (or no longer) connected — the viewport state
    was still updated server-side.
    """
    from app.project_sessions import session_project
    manager = getattr(_app_main, "manager", None)
    if manager is None:
        return False
    for connection in list(manager.active_connections):
        if session_project(connection) != project_id:
            continue
        client_id = manager.connection_clients.get(connection)
        if not client_id:
            continue
        message = {"type": "canvas_viewport_command", "project_id": project_id, **command}
        await manager.send_personal_message(message, client_id)
        return True
    return False


def _open_canvas(project_id: str) -> dict:
    """Validate the project and return its canvas dict."""
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    return opened.get("canvas", {})


def _find_node(project_id: str, node_id: str) -> dict:
    """Find a node by ID on the canvas, or raise 404."""
    canvas = _open_canvas(project_id)
    for node in canvas.get("nodes", []):
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            return node
    raise agent_error(404, "NODE_NOT_FOUND", f"Node not found: {node_id}",
                      "先调用 canvas.list_nodes 查看画布上的真实节点 ID。")


@router.get("/nodes")
async def list_nodes(project_id: str, request: Request = None, x_agent_project: str = Header(default="")):
    """Read v2 projections; repeated fields query keys select optional blocks."""
    query = request.query_params if request is not None else {}
    return await list_nodes_core(
        project_id, group_id=query.get("group_id"), created_by=query.get("created_by"),
        fields=query.getlist("fields") if "fields" in query else None,
    )


async def list_nodes_core(project_id: str, group_id=None, fields=None, created_by=None) -> dict:
    """Request-free read shared by REST and MCP; never backfill legacy data."""
    return project_nodes(_open_canvas(project_id), group_id=group_id, fields=fields, created_by=created_by)


def _snapshot_node(canvas: dict, node_id: str) -> dict:
    for node in canvas.get("nodes", []):
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            return node
    raise agent_error(404, "NODE_NOT_FOUND", f"Node not found: {node_id}",
                      "先调用 canvas.list_nodes 查看画布上的真实节点 ID。")


def _creation_context(request: Request, agent_node_id="") -> str:
    """Existing transport context, not a new authentication mechanism."""
    ownership_fields(agent_node_id)  # Reject invalid identity types/blank strings.
    if request.headers.get("X-Agent-Project", "") and not agent_node_id:
        raise agent_error(400, "INVALID_ARGUMENT", "agent_node_id is required for Agent creation",
                          "请传入发起本次操作的 Agent 节点 ID。")
    return agent_node_id


@router.get("/nodes/{node_id}")
async def get_node(project_id: str, node_id: str, x_agent_project: str = Header(default="")):
    """Get detailed information about a specific node."""
    return _find_node(project_id, node_id)


@router.post("/nodes")
async def create_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a new node on the canvas. 可选 `agent_node_id`（#356 D11）：传了则
    未显式给 x/y 时自动落位；Agent 缺少会话框时自动建立（与 MCP 同行为）。"""
    body = await request.json()
    agent = _creation_context(request, body.get("agent_node_id", ""))
    return await _rest_agent_call(request, project_id, "canvas.create_node", body,
                                  lambda: create_node_core(project_id, body.get("node", {}), agent_node_id=agent), agent)


@router.patch("/nodes/{node_id}")
async def update_node(project_id: str, node_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Update an existing node's properties."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "canvas.update_node", {"node_id": node_id, "updates": body},
                                  lambda: update_node_core(project_id, node_id, body))


@router.delete("/nodes/{node_id}")
async def delete_node(project_id: str, node_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Delete a node from the canvas. #353：若该节点有 agent 宿主会话，同步杀进程并
    摘除会话（REST 与 MCP 共用本协程，单点覆盖；非 agent 节点 no-op）。
    #357：确认门移到服务端——带 X-Agent-Project 的 agent 写操作先过门（409=拒/超时）；
    浏览器用户路径不进本门（前端 #347 ffDialog 已确认）。"""
    _validate_project(project_id)
    await _server_confirm_gate(request, project_id, "canvas.delete_node",
                               {"node_id": node_id}, f"删除画布节点 {node_id}")
    return await _rest_agent_call(request, project_id, "canvas.delete_node", {"node_id": node_id},
                                  lambda: delete_node_core(project_id, node_id), agent_node_id)


async def delete_node_core(project_id: str, node_id: str) -> dict:
    """Delete the canvas first; session cleanup is separately irreversible."""
    from app.services.agent_ops_context import current_agent_ops_context
    context = current_agent_ops_context()
    agent = (context and context.project_id == project_id and context.agent_node_id
             and context.tool == "canvas.delete_node")
    store = _app_main.FIREFLY_PROJECT_STORE
    deletion = {"node_id": node_id, "committed": False}
    if agent:
        set_agent_generation_running(store, project_id, context.op_id, context.op_id, True)
    try:
        mutation_error = None
        try:
            await _apply_mutations(project_id, [{"op": "remove_node", "node_id": node_id}], deletion=deletion)
        except HTTPException as exc:
            # Do not touch session artifacts unless a failed flush still proved deletion.
            if not deletion["committed"]:
                raise
            mutation_error = exc
        try:
            if agent:
                await _cleanup_agent_deleted_node(store, project_id, node_id, context)
            else:
                await _cleanup_deleted_node(project_id, node_id)
        except Exception as exc:
            # Session cleanup can be partial; preserve a prior canvas error or forbid blind retry.
            if mutation_error is not None:
                from app.core import logger as _logger
                _logger.warning("delete cleanup failed for %s: %s", node_id, exc)
            elif agent:
                raise agent_error(500, "INTERNAL_ERROR",
                                  f"Operation {context.op_id} session cleanup outcome is unknown: {exc}",
                                  "先检查画布、操作日志和会话，再决定是否重试。", retryable=False,
                                  details={"op_id": context.op_id, "status": "partial-unknown"}) from exc
            else:
                raise
        if mutation_error is not None:
            raise mutation_error
        return _op_result({"success": True, "node_id": node_id})
    finally:
        if agent:
            set_agent_generation_running(store, project_id, context.op_id, context.op_id, False)


def _delete_session_effect(store, project_id, context, effect, *, create):
    """Persist the uncertainty before cleanup, under the same lock as undo."""
    from app.services import agent_ops_store
    from firefly_opsdb import OpsDb
    db = OpsDb(store.root)
    with store._lock:
        # An append can commit and then raise; do not modify a row while the
        # journal may still compare it with its original pending record.
        journal = _JOURNALS.get(store)
        entry = None
        if journal is not None:
            with journal._lock:
                entry = journal._entries.get((project_id, context.op_id))
                if entry is not None and entry["status"] != "settled":
                    raise RuntimeError("canvas operation has not settled; session cleanup skipped")
        record = agent_ops_store.get_operation(db, project_id, context.op_id)
        if record is None:
            if entry is not None:
                raise RuntimeError("settled canvas operation is missing; session cleanup skipped")
            if not create:
                raise RuntimeError("session cleanup operation is missing")
            _append_effect_preflight(store, project_id, {
                "op_id": context.op_id, "turn_id": context.turn_id,
                "agent_node_id": context.agent_node_id, "tool": context.tool, "args": context.args,
                "before": [], "after": [], "touched_node_ids": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "irreversible_effects": [effect], "undone": False,
            })
            return
        effects = list(record["irreversible_effects"])
        if create:
            effects.append(effect)
        else:
            effects[-1] = effect
        with db.project_lock(project_id), db.connect(project_id, create_project_dir=False) as conn, conn:
            cursor = conn.execute(
                "UPDATE agent_ops_operations SET irreversible_effects_json = ? "
                "WHERE project_id = ? AND op_id = ? AND undone = 0",
                (json.dumps(effects, ensure_ascii=False), project_id, context.op_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("session cleanup operation was undone or removed")


async def _cleanup_agent_deleted_node(store, project_id, node_id, context):
    from app.services.agent_host import agent_host_manager as manager
    key = f"{project_id}:{node_id}"
    # drop_node_session only removes files when a registered session exists.
    # Do not call it after observing no session: one could appear before its pop.
    async with manager._lock:
        session = manager.sessions.get(key)
        if session is None:
            reset_generation_counts(project_id, node_id)
            return
        effect = {"type": "agent_session_cleanup", "node_id": node_id,
                  "outcome": "unknown; cleanup may not have started or may be partial",
                  "impact": "session registration and process state may be lost; session directory and files may be deleted; canvas undo cannot restore them"}
        _delete_session_effect(store, project_id, context, effect, create=True)
    directory = Path(manager._get_project_dir(project_id)) / "agent_nodes" / str(node_id)
    process = session.process
    session_status_before = session.status
    try:
        process_before = "no process" if process is None else ("running" if process.poll() is None else "exited")
    except OSError:
        process_before = "unknown"
    try:
        before_dir = directory.exists()
    except OSError:
        before_dir = None
    try:
        dropped = await manager.drop_node_session(project_id, node_id)
        outcome = "cleanup returned" if dropped else "no session was dropped (concurrent change possible)"
    except (OSError, RuntimeError) as exc:
        outcome = f"cleanup failed; partial outcome possible: {type(exc).__name__}: {exc}"
    finally:
        reset_generation_counts(project_id, node_id)
    try:
        after_dir = directory.exists()
        directory_state = ("absent after cleanup" if not after_dir else "still exists; contents may have been partially removed")
        if before_dir is False:
            directory_state = "absent before cleanup; " + directory_state
    except OSError:
        directory_state = "unknown"
    try:
        process_state = "no process before cleanup" if process is None else ("running" if process.poll() is None else "exited")
    except OSError:
        process_state = "unknown"
    async with manager._lock:
        registration = "original session registered" if manager.sessions.get(key) is session else "original session no longer registered"
    effect.update(outcome=outcome, directory_state=directory_state,
                  process_state_before=process_before, process_state=process_state,
                  session_status_before=session_status_before, session_status_after=session.status,
                  registration_state=registration,
                  observation="process and registration refer to the session observed before cleanup; concurrent replacement is possible")
    _delete_session_effect(store, project_id, context, effect, create=False)


async def _cleanup_deleted_node(project_id: str, node_id: str) -> None:
    try:
        from app.services.agent_host import agent_host_manager
        await agent_host_manager.drop_node_session(project_id, node_id)
    except Exception as exc:
        from app.core import logger as _logger
        _logger.warning("#353 drop_node_session failed for %s: %s", node_id, exc)
    reset_generation_counts(project_id, node_id)


@router.post("/edges")
async def create_edge(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a connection between two nodes."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "canvas.connect", body,
                                  lambda: create_edge_core(project_id, body.get("edge", {})))


@router.delete("/edges/{from_node}/{to_node}")
async def delete_edge(project_id: str, from_node: str, to_node: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Remove a connection between two nodes. #357：agent 调用同样过确认门。"""
    _validate_project(project_id)
    await _server_confirm_gate(request, project_id, "canvas.delete_edge",
                               {"from": from_node, "to": to_node},
                               f"删除连线 {from_node} → {to_node}")
    return await _rest_agent_call(request, project_id, "canvas.disconnect", {"from_node": from_node, "to_node": to_node},
                                  lambda: delete_edge_core(project_id, from_node, to_node), agent_node_id)


async def delete_edge_core(project_id: str, from_node: str, to_node: str) -> dict:
    """#357：删连线执行核（无门）。先找边后删，不存在 404。"""
    # Find the edge first
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    connections = canvas.get("connections", [])
    target_edge = None
    for edge in connections:
        if isinstance(edge, dict) and str(edge.get("from", "")) == from_node and str(edge.get("to", "")) == to_node:
            target_edge = edge
            break
    if target_edge is None:
        raise agent_error(404, "EDGE_NOT_FOUND", f"Edge not found: {from_node} -> {to_node}",
                          "这条连线不存在；先调用 canvas.list_nodes 查看现有连线。")

    await _apply_mutations(project_id, [{"op": "remove_edge", "edge_id": target_edge.get("id", "")}])
    return _op_result({"success": True, "from": from_node, "to": to_node})


@router.post("/view")
async def canvas_view(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """#266 canvas.view: 整页/选区媒体导出为一张图喂模型（只读，contact sheet base64）。"""
    body = await request.json()
    return await canvas_view_core(project_id, body.get("scope") or "page", body.get("node_ids"), body.get("max_px"))


@router.post("/view_media")
async def canvas_view_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """#266 canvas.view_media: 查看单节点媒体（图像下缩放 / 视频抽帧拼图，只读）。"""
    body = await request.json()
    return await view_media_core(project_id, body.get("node_id"), body.get("max_px"), body.get("frames"))


@router.post("/edit_crop")
async def canvas_edit_crop(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#266 canvas.edit_crop: 本地裁节点图选区覆写节点图（写操作）。
    #357：确认门移到服务端（带 X-Agent-Project 时）；浏览器路径前端 ffDialog 已确认。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_crop", body,
                               f"裁剪节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_crop", body,
                                  lambda: crop_node_image_core(project_id, body.get("node_id"), body.get("rect")), agent_node_id)


@router.post("/batch_edit")
async def canvas_batch_edit(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """#266 canvas.batch_edit（§59.3）：N 节点 + 一句 prompt = N 次生成（调既有生成通路，消耗生成任务）。
    #351：agent 调用过确认门并按张数计会话限额。"""
    body = await request.json()
    node_ids = body.get("node_ids") or []
    await _server_confirm_gate(request, project_id, "canvas.batch_edit", body,
                               f"批量生成 {len(node_ids)} 个节点（消耗外部生成额度）")
    if request.headers.get("X-Agent-Project", ""):
        enforce_generation_limit(project_id, request.query_params.get("agent_node_id", ""),
                                 count=max(1, len(node_ids)))
    agent = _creation_context(request, request.query_params.get("agent_node_id", ""))
    return await _rest_agent_call(request, project_id, "canvas.batch_edit", body,
                                  lambda: batch_edit_core(project_id, body.get("node_ids"), body.get("prompt"),
                                                          agent_node_id=agent), agent)


@router.post("/edit_rotate")
async def canvas_edit_rotate(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#266b canvas.edit_rotate: 本地旋转节点图（90/180/270 逆时针）覆写节点图。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_rotate", body,
                               f"旋转节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_rotate", body,
                                  lambda: rotate_node_image_core(project_id, body.get("node_id"), body.get("angle")), agent_node_id)


@router.post("/edit_flip")
async def canvas_edit_flip(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#266b canvas.edit_flip: 本地镜像节点图（horizontal 左右 / vertical 上下）覆写节点图。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_flip", body,
                               f"镜像节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_flip", body,
                                  lambda: flip_node_image_core(project_id, body.get("node_id"), body.get("direction")), agent_node_id)


@router.post("/edit_outpaint")
async def canvas_edit_outpaint(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#289 canvas.edit_outpaint: 服务端复刻站内红底扩图链（PIL 填 #ff0044 + 贴原图）覆写节点图。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_outpaint", body,
                               f"红底扩图节点 {body.get('node_id')}：扩大画布（#ff0044 填充外区）并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_outpaint", body,
                                  lambda: outpaint_node_image_core(project_id, body.get("node_id"), body.get("outpaint_size"), body.get("offset")), agent_node_id)


# =============================================================================
# Core (Request-free) implementations — shared by the HTTP endpoints above and
# the MCP streamable-HTTP endpoint (app/routers/api_mcp.py, #243 / ADR 0022).
# =============================================================================


async def create_node_core(project_id: str, node: dict, agent_node_id: str = "") -> dict:
    """Create a node and return the geometry committed by the placement mutation."""
    _validate_project(project_id)
    if not isinstance(node, dict):
        raise agent_error(400, "INVALID_ARGUMENT", "node must be an object",
                          "canvas.create_node 的 node 参数需是对象。")

    reserved = sorted(key for key in ownership_fields() if key in node)
    if reserved:
        raise agent_error(400, "INVALID_ARGUMENT", "Creation metadata is server-owned",
                          "不要传归属或人工编辑保护字段；服务端按本次调用上下文写入。",
                          details={"rejected_keys": reserved})
    node = {**node, **ownership_fields(agent_node_id)}
    # #355 W2：caller 传了已存在于画布的 id → 报错，不再静默 upsert 覆盖。
    # #343 升位：MCP/pi 工具建节点一律不接受调用方指定 id（服务端生成）——
    # 任意 id 会流入宿主 argv（--name/--session-dir），传任何非空 id 直接 400。
    if str(node.get("id") or ""):
        raise agent_error(
            400, "INVALID_ARGUMENT",
            "create_node does not accept caller-specified node id; the server generates ids",
            "不要传 node.id；去掉 id 字段重试，服务端会生成并返回真实 id。")
    if not node.get("id"):
        node["id"] = f"node-{uuid.uuid4().hex[:12]}"
    if not node.get("type"):
        raise agent_error(400, "INVALID_ARGUMENT", "node.type is required",
                          "建节点必须带 type（如 smart-image / prompt / smart-group）。")
    explicit_position = any(key in node for key in ("x", "y"))
    node["created_at"] = _app_main.now_ms()
    frame_id = f"node-{uuid.uuid4().hex[:12]}" if agent_node_id and not explicit_position else None

    def build(canvas):
        nonlocal created
        nodes = _placement_nodes(canvas)
        agent = nodes.get(agent_node_id, {})
        bindings = []
        frame = None
        if not explicit_position and agent.get("type") in (NODE_TYPE_AGENT, "agent"):
            frame = nodes.get(str(agent.get("chatFrameId") or ""))
            if not frame or frame.get("type") != NODE_TYPE_GROUP:
                x, y = agent_frame.right_of_canvas(nodes.values()), agent_frame.coordinate(agent.get("y"))
                frame = {"id": frame_id, "type": NODE_TYPE_GROUP, "x": x, "y": y, "w": 800, "h": 360,
                         "title": f"{agent.get('title') or 'Agent'} · 会话框", "items": [],
                         "chatFrame": True, "chatFrameFor": agent_node_id,
                         "created_at": node["created_at"], **ownership_fields(agent_node_id)}
                # Binding, frame and card belong to the same CAS write and undo delta.
                bindings.append({"op": "upsert_node", "node": {**agent, "chatFrameId": frame_id}})

        w, h = agent_frame.node_size(node, grouped=frame is not None)
        created = {**node, "w": w, "h": h}
        if frame is not None:
            created, frame = agent_frame.place_in_frame(frame, created, nodes)
            bindings.append({"op": "upsert_node", "node": frame})
        elif explicit_position:
            created.update(x=node.get("x", 0), y=node.get("y", 0))
        else:
            created.update(x=agent_frame.right_of_canvas(nodes.values()), y=0)
        return [{"op": "upsert_node", "node": created}, *bindings]

    created = None
    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result({"success": True, "node_id": node["id"], **_geometry(created)})


def _placement_nodes(canvas):
    return {node["id"]: node for node in canvas.get("nodes", [])}


def _geometry(node):
    return {key: node[key] for key in ("x", "y", "w", "h")}


async def update_node_core(project_id: str, node_id: str, updates: dict) -> dict:
    """Update an existing node's properties (`updates` is a flat field dict)."""
    if not isinstance(updates, dict):
        raise agent_error(400, "INVALID_ARGUMENT", "updates must be an object",
                          "canvas.update_node 的 updates 需是 {字段: 值} 对象。")
    # #355 W1：可更新字段白名单（清单在 manifest，两路共用）；白名单外一律 400。
    illegal = sorted(k for k in updates if k not in _UPDATABLE_NODE_FIELDS)
    if illegal:
        raise agent_error(
            400, "INVALID_ARGUMENT",
            "update_node 字段白名单外的键被拒: " + ",".join(illegal),
            "只白名单内字段可改（x/y/w/h/title/locked 等）；先调用 canvas.get_node 查看当前字段。",
            details={"rejected_keys": illegal})
    for key in ("x", "y", "w", "h"):
        if key in updates and (isinstance(updates[key], bool) or not isinstance(updates[key], (int, float))):
            raise agent_error(400, "INVALID_ARGUMENT", f"{key} must be a number",
                              f"{key} 需是数字；重新传入。")
    _validate_project(project_id)
    updated_at = _app_main.now_ms()

    def build(canvas):
        target_node = _snapshot_node(canvas, node_id)
        # Session-frame membership is maintained only by the placement chain.
        if "items" in updates and target_node.get("type") == "smart-group" and target_node.get("chatFrame") is True:
            raise agent_error(400, "INVALID_ARGUMENT",
                              "会话框 items 由服务端落位链维护，canvas.update_node 不可覆写",
                              "会话框成员由服务端维护；不要改 items，用 canvas.create_node 在会话框内建节点。")
        from app.services.agent_ops_context import current_agent_ops_context
        from app.services.agent_ops_delta import _equal
        context = current_agent_ops_context()
        if (context and context.project_id == project_id and context.agent_node_id
                and context.tool in _JOURNALED_TOOLS
                and all(key in target_node and _equal(target_node[key], value)
                        for key, value in updates.items())):
            return []
        patched = {**target_node, **updates, "updated_at": updated_at}
        return [{"op": "upsert_node", "node": patched}]

    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result({"success": True, "node_id": node_id})


async def create_edge_core(project_id: str, edge: dict) -> dict:
    """Create a connection between two nodes. `edge` needs `from` and `to`."""
    _validate_project(project_id)
    if not isinstance(edge, dict):
        raise agent_error(400, "INVALID_ARGUMENT", "edge must be an object",
                          "canvas.create_edge 的 edge 需是含 from/to 的对象。")

    if not edge.get("id"):
        edge["id"] = f"edge-{uuid.uuid4().hex[:12]}"
    if not edge.get("kind"):
        edge["kind"] = "flow"  # #356 D12：缺省归服务端（两路一致）；前端总传 kind 不受影响
    if not edge.get("from") or not edge.get("to"):
        raise agent_error(400, "INVALID_ARGUMENT", "edge.from and edge.to are required",
                          "连线需要两个端点 node id；先调用 canvas.list_nodes 获取。")

    await _apply_mutations(project_id, [{"op": "upsert_edge", "edge": edge}])
    return _op_result({"success": True, "edge_id": edge["id"]})


# =============================================================================
# Generate tools
# =============================================================================

# #351：花钱生成类工具每 agent 会话限额（generate.trigger 计 1，batch_edit 计张数）。
# 内存计数，键 (project_id, agent_node_id)；节点删除/会话 drop 时清理。
GENERATION_SESSION_LIMIT = int(os.environ.get("FIREFLY_AGENT_GENERATION_LIMIT", "20"))
_GENERATION_COUNTS: dict = {}


def enforce_generation_limit(project_id: str, agent_node_id: str, count: int = 1) -> None:
    """#351：超上限抛 HTTPException 429；调用方（agent 入口）负责在门后计数。"""
    key = (project_id, str(agent_node_id or ""))
    with CANVAS_TASK_LOCK:
        used = int(_GENERATION_COUNTS.get(key, 0))
        if used + count > GENERATION_SESSION_LIMIT:
            raise agent_error(
                429, "BUDGET_EXCEEDED",
                (f"本 agent 会话生成额度 {used}/{GENERATION_SESSION_LIMIT}，"
                 f"本次需 {count} 张，超限拒绝"),
                "额度已用尽；请新开会话或由用户重置额度后继续。")
        _GENERATION_COUNTS[key] = used + count


def reset_generation_counts(project_id: str, agent_node_id: str = "") -> None:
    """#351：会话/节点销毁时清计数（canvas delete_node_core 与 agent-nodes
    DELETE stop_agent_node 两处调用；本模块注释曾称 drop_node_session 也调——
    与调用点不符，已改实）。"""
    with CANVAS_TASK_LOCK:
        for key in [k for k in _GENERATION_COUNTS
                    if k[0] == project_id and (not agent_node_id or k[1] == agent_node_id)]:
            _GENERATION_COUNTS.pop(key, None)


@generate_router.post("/trigger")
async def trigger_generation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Trigger generation on a node, returns task ID.
    #351：agent 调用（带 X-Agent-Project）先过服务端确认门 + 会话限额；
    浏览器用户路径不进本门（用户主动点击，由前端发起）。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "generate.trigger",
                               body, f"节点 {body.get('node_id', '')} 生成图片（消耗外部生成额度）")
    if request.headers.get("X-Agent-Project", ""):
        enforce_generation_limit(project_id, request.query_params.get("agent_node_id", ""),
                                 count=max(1, int(body.get("n") or 1)))
    agent = _creation_context(request, request.query_params.get("agent_node_id", ""))
    return await _rest_agent_call(request, project_id, "generate.trigger", body,
                                  lambda: trigger_generation_core(project_id, body.get("node_id"), agent_node_id=agent), agent)


def _batch_register_reserved(journal, project_id, record, delta):
    from app.services import agent_ops_store
    from copy import deepcopy

    agent_ops_store._validate_record(record)
    agent_ops_store._json_value(delta)
    key = (project_id, record['op_id'])
    with journal._lock:
        existing = journal._entries.get(key)
        if existing is not None:
            if existing['record'] != record or existing['delta'] != delta:
                raise ValueError('operation already registered')
            return key
        journal._entries[key] = {
            'project_id': project_id, 'op_id': record['op_id'],
            'status': 'pending', 'conflicts': [], 'error': None,
            'record': deepcopy(record), 'delta': deepcopy(delta), 'attempted': False,
        }
    return key


def _generation_mutation(store, project_id, context, task_id, guarded, action, *, parent_id=None, effects=()):
    """Capture the persistence projection while undo and timer flushes are excluded."""
    from app.services.agent_ops_delta import build_delta, touched_node_ids
    from app.services.agent_ops_journal import record_sides
    from app.services import agent_ops_store

    journal = _journal_for(store)
    batch = _BATCH_GENERATION.get()
    lifecycle = _MEDIA_LIFECYCLE.get()
    reserved = batch is not None or lifecycle is not None
    with journal.mutation(project_id) as operation:
        # Reserve before the write so a failing reservation cannot orphan a delta.
        if reserved:
            reservation = {"op_id": context.op_id, "turn_id": context.turn_id}
            if parent_id is not None:
                reservation["parent_op_id"] = parent_id
            try:
                agent_ops_store._reserve(journal, journal._opsdb, project_id, reservation)
            except BaseException:
                agent_ops_store._release(journal, journal._opsdb, project_id, context.op_id)
                raise
        before = store._project_canvas_for_persistence(store._canvases[project_id])
        action_error = None
        try:
            result = action()
        except BaseException as exc:
            action_error = exc
            result = None
        after = store._project_canvas_for_persistence(store._canvases[project_id])
        delta = build_delta(before, after)
        if not delta and not effects:
            if reserved:
                agent_ops_store._release(journal, journal._opsdb, project_id, context.op_id)
            if action_error is not None:
                raise action_error
            return result
        old, new = record_sides(delta)
        record = {"op_id": context.op_id, "turn_id": context.turn_id,
                  "agent_node_id": context.agent_node_id, "tool": context.tool,
                  "args": context.args, "before": old, "after": new,
                  "touched_node_ids": touched_node_ids(delta),
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "irreversible_effects": list(effects), "undone": False}
        if parent_id is not None:
            record["parent_op_id"] = parent_id
        if batch is not None:
            # Every visible family member stays guarded until every batch task ends.
            batch["ids"].add(context.op_id)
            if context.op_id == batch["root"]:
                batch["root_exists"] = True
            for token in batch["tokens"]:
                set_agent_generation_running(store, project_id, context.op_id, token, True)
        else:
            set_agent_generation_running(store, project_id, context.op_id, task_id, True)
        guarded.add(context.op_id)
        listener = None
        if lifecycle is not None:
            lifecycle['guarded'].update(guarded if batch is None else {context.op_id})
            if delta:
                from app.services.agent_ops_journal import _conflicts
                def listener(pid, canvas):
                    if pid == project_id and not _conflicts(canvas, delta):
                        lifecycle['touched'].update(touched_node_ids(delta))
                        lifecycle['updated_at'] = canvas.get('updated_at')
                store.add_persisted_canvas_listener(listener)
                lifecycle['listeners'].append(listener)
        try:
            if not reserved:
                operation.register(record, delta)
            else:
                pending = batch['unregistered'] if batch is not None else lifecycle['unregistered']
                pending[context.op_id] = (record, delta)
                operation._key = _batch_register_reserved(journal, project_id, record, delta)
                pending.pop(context.op_id)
        except BaseException:
            # Without a registered delta no later flush can prove settlement.
            store._agent_generation_unregistered.add((project_id, context.op_id))
            raise
    try:
        try:
            store.flush(project_id)
            status = operation.result()
            if status["status"] not in ("settled", "append_failed"):
                raise RuntimeError(f"Generation operation {context.op_id} journal {status['status']}: {status['error'] or status['conflicts']}")
        except BaseException as exc:
            if action_error is not None:
                action_error.add_note(f"Generation operation {context.op_id} settlement failed: {exc}")
                raise action_error from exc
            raise
    finally:
        if listener is not None and operation.result()['status'] in ('settled', 'append_failed'):
            store.remove_persisted_canvas_listener(listener)
            lifecycle['listeners'].remove(listener)
    if action_error is not None:
        raise action_error
    return result


def _generation_child(store, project_id, parent, task_id, guarded, action, *, effects=(), lifecycle=None):
    from app.services.agent_ops_context import agent_ops_scope
    with agent_ops_scope(project_id=project_id, agent_node_id=parent.agent_node_id,
                         turn_id=parent.turn_id, tool=parent.tool, args=parent.args) as child:
        if lifecycle is not None:
            lifecycle['op_id'] = child.op_id
        return _generation_mutation(store, project_id, child, task_id, guarded, action,
                                    parent_id=parent.op_id, effects=effects)


def _generation_release(store, project_id, task_id, guarded, *, release=True):
    with store._lock:
        batch = _BATCH_GENERATION.get()
        if any((project_id, op_id) in store._agent_generation_unregistered for op_id in guarded):
            return False
        canvas = store._canvases.get(project_id, {})
        if any(item.get('taskId') == task_id for node in canvas.get('nodes', [])
               for item in node.get('pendingTasks', []) if isinstance(item, dict)):
            return False
        for journal in store._agent_ops_journals:
            with journal._lock:
                if any(entry['status'] not in ('settled', 'append_failed')
                       for (pid, op_id), entry in journal._entries.items()
                       if pid == project_id and op_id in guarded):
                    return False
        if not release:
            return True
        for op_id in batch["ids"] if batch is not None else guarded:
            set_agent_generation_running(store, project_id, op_id, task_id, False)
        if batch is not None:
            batch["tokens"].discard(task_id)
        return True


async def _generation_archive_to_completion(coro):
    from app.services.agent_ops_async import await_to_completion
    # The archive coroutine itself starts store threads and owns temporary files.
    return await await_to_completion(coro)


async def _generation_event(project_id, lifecycle, phase):
    if lifecycle['persisted']:
        if phase == 'end' and not lifecycle.get('start_sent'):
            await _generation_event(project_id, lifecycle, 'start')
        if phase == 'start' and lifecycle.get('start_sent'):
            return
        await _broadcast_canvas_updated(project_id, source='agent', updated_at=lifecycle['updated_at'],
                                        op_id=lifecycle['op_id'], touched_node_ids=[lifecycle['node_id']],
                                        phase=phase)
        if phase == 'start':
            lifecycle['start_sent'] = True


async def _generation_finish(store, project_id, task_id, guarded, lifecycle):
    if lifecycle.get('pending') or not _generation_release(store, project_id, task_id, guarded, release=False):
        return False
    try:
        await _generation_archive_to_completion(_generation_event(project_id, lifecycle, 'end'))
    finally:
        store.remove_persisted_canvas_listener(lifecycle['listener'])
        _generation_release(store, project_id, task_id, guarded)
    return True


def _schedule_generation_worker(create_task, *args, pre_cancel=False):
    lifecycle = args[-1]
    task = create_task(_run_agent_generation(*args, pre_cancel=pre_cancel))
    def recover_unstarted(done):
        if done.cancelled() and not lifecycle.get('worker_entered'):
            with _LIFETIME_LOCK:
                stopping = done.get_loop() in _LIFETIME_STOPPING
            if stopping:
                task_id = args[0]
                with CANVAS_TASK_LOCK:
                    if task_id in CANVAS_TASKS:
                        CANVAS_TASKS[task_id].update(status='cleanup_pending',
                            error='Generation cleanup interrupted by shutdown; outcome uncertain', updated_at=time.time())
            else:
                _schedule_generation_worker(create_task, *args, pre_cancel=True)
    task.add_done_callback(recover_unstarted)
    return _track_lifetime_task(task)


async def trigger_generation_core(project_id: str, node_id: str, agent_node_id: str = "") -> dict:
    """Trigger generation on a node, returns task ID (Request-free core)."""
    _validate_project(project_id)
    creation = ownership_fields(agent_node_id)
    if not node_id:
        raise agent_error(400, "INVALID_ARGUMENT", "node_id is required",
                          "先调用 canvas.list_nodes 获取节点 ID。")

    target_node = _find_node(project_id, node_id)

    node_type = str(target_node.get("type", ""))
    prompt = str(target_node.get("prompt", "") or target_node.get("text", ""))
    if not prompt:
        raise agent_error(400, "NODE_NOT_GENERATABLE", f"Node {node_id} has no prompt",
                          "先给节点写 prompt（canvas.update_node 或连提示词节点），再触发生成。")

    # Route through model routing (ADR 0006): the router picks provider + model
    # from the node's generation parameters; no hardcoded provider/size.
    reference_asset_ids = [str(a) for a in (target_node.get("reference_asset_ids") or [])]
    gen_request = ImageGenerationRequest(
        prompt=prompt,
        size=str(target_node.get("generation_size") or "1024x1024"),
        width=target_node.get("width"),
        height=target_node.get("height"),
        aspect_ratio=str(target_node.get("aspect_ratio") or "1:1"),
        resolution=str(target_node.get("generation_resolution") or "1k"),
        reference_asset_ids=tuple(reference_asset_ids),
        media_type="image",
        quality="auto",
        count=1,
    )
    try:
        route = _app_main.route_image_generation(
            gen_request, _app_main.load_api_providers(), provider_id="", model=""
        )
    except ModelRoutingError as exc:
        raise agent_error(400, "INVALID_ARGUMENT", exc.to_dict()["message"],
                          "换一个可用的 provider/model 或调整参数后重试。",
                          details={"routing": exc.to_dict()}) from exc

    # Convert reference asset IDs to image references for the provider call.
    reference_images = []
    if reference_asset_ids:
        try:
            ref_records = await asyncio.to_thread(
                _app_main.project_image_references, project_id, reference_asset_ids
            )
        except Exception as exc:
            raise agent_error(400, "ASSET_NOT_FOUND", str(exc),
                              "参考图资产不可用；先调用 media.list 确认 asset id。") from exc
        reference_images = [
            AIReference(
                url=str(r.get("url") or ""),
                name=str(r.get("name") or ""),
                kind=str(r.get("kind") or "image"),
                mime=str(r.get("mime") or "image/png"),
            )
            for r in ref_records
        ]

    payload = {
        "prompt": prompt,
        "provider_id": route.provider_id,
        "model": route.model,
        "size": gen_request.size,
        "quality": "auto",
        "n": 1,
        "reference_images": reference_images,
    }

    task_id = f"agent_img_{uuid.uuid4().hex}"
    result_node_id = f"node-{uuid.uuid4().hex[:12]}"
    result_template = {
        "id": result_node_id,
        "type": "smart-image",
        "title": f"生成结果 · {prompt[:24]}",
        **creation,
        "created_at": _app_main.now_ms(),
    }
    result_node = None

    def build_result(canvas):
        nonlocal result_node
        nodes = _placement_nodes(canvas)
        source = nodes.get(node_id)
        if source is None:
            raise agent_error(404, "NODE_NOT_FOUND", f"Node not found: {node_id}",
                              "先调用 canvas.list_nodes 取得当前生成节点。")
        groups = [n for n in nodes.values() if n.get("type") == NODE_TYPE_GROUP]
        frame = next((g for g in groups if node_id in (g.get("items") or [])), None)
        w, h = agent_frame.node_size(result_template, grouped=frame is not None)
        result_node = {**result_template, "w": w, "h": h}
        if frame is not None:
            result_node, frame = agent_frame.place_in_frame(frame, result_node, nodes)
            return [{"op": "upsert_node", "node": result_node}, {"op": "upsert_node", "node": frame}]
        memberships = {identity for group in groups for identity in group.get("items") or []}
        result_node = agent_frame.next_to_source(source, result_node, nodes.values(), memberships)
        return [{"op": "upsert_node", "node": result_node}]

    from app.services.agent_ops_context import current_agent_ops_context, create_agent_ops_task
    from app.services.agent_ops_async import run_blocking_to_completion
    context = current_agent_ops_context()
    batch = _BATCH_GENERATION.get()
    if not (context and context.project_id == project_id and context.agent_node_id
            and (context.tool == "generate.trigger" or
                 (context.tool == "canvas.batch_edit" and batch is not None))):
        context = None
    store = _app_main.FIREFLY_PROJECT_STORE
    guarded = set()
    started = False
    recovery_required = False
    start_cancel = None
    if context:
        lifecycle = {'op_id': context.op_id, 'node_id': result_node_id, 'persisted': False, 'pending': False, 'updated_at': None}
        def persisted_listener(pid, canvas):
            if pid != project_id:
                return
            if lifecycle['persisted'] or any(n.get('id') == result_node_id for n in canvas.get('nodes', [])):
                pending = any(item.get('taskId') == task_id for node in canvas.get('nodes', [])
                              for item in node.get('pendingTasks', []) if isinstance(item, dict))
                lifecycle.update(persisted=True, pending=pending, updated_at=canvas.get('updated_at'))
        lifecycle['listener'] = persisted_listener
        store.add_persisted_canvas_listener(persisted_listener)
        if batch is not None:
            with store._lock:
                batch["tokens"].add(task_id)
                for op_id in batch["ids"]:
                    set_agent_generation_running(store, project_id, op_id, task_id, True)

        def cleanup_start_error(exc):
            store.flush(project_id)
            node = store.find_canvas_node(project_id, result_node_id) or {}
            if any(item.get('taskId') == task_id for item in node.get('pendingTasks', [])):
                failure = {"id": f"generation-{uuid.uuid4().hex}", "task_id": task_id,
                           "type": "online-image", "status": "failed", "prompt": prompt,
                           "created_at": _app_main.now_ms(), "completed_at": _app_main.now_ms(),
                           "outputs": [], "result_node_id": result_node_id,
                           "source_node_id": node_id, "error": str(exc)}
                _generation_child(store, project_id, context, task_id, guarded,
                    lambda: store.fail_image_generation(project_id, result_node_id, task_id,
                                                        failure, require_pending=True))
            if not _generation_release(store, project_id, task_id, guarded, release=False):
                raise _JournalUnsettled("generation cleanup has unsettled journal entries")

        async def recover_start_error(exc):
            from app.core import logger as _logger
            while True:
                try:
                    await run_blocking_to_completion(cleanup_start_error, exc)
                    if await _generation_finish(store, project_id, task_id, guarded, lifecycle):
                        with CANVAS_TASK_LOCK:
                            CANVAS_TASKS[task_id].update(status="failed", error=str(exc), updated_at=time.time())
                        return
                    raise _JournalUnsettled('generation cleanup not persisted')
                except (OSError, sqlite3.Error, _JournalUnsettled) as retry_error:
                    _logger.warning("generation cleanup retry task=%s op=%s: %s", task_id, context.op_id, retry_error)
                    await asyncio.sleep(1)

        def start():
            nonlocal started, recovery_required
            try:
                def create_result():
                    canvas = store.canvas(project_id)
                    return store.apply_mutations(project_id, build_result(canvas), base_revision=canvas["revision"])
                if batch is not None and batch["root_exists"]:
                    _generation_child(store, project_id, context, task_id, guarded, create_result, lifecycle=lifecycle)
                else:
                    _generation_mutation(store, project_id, context, task_id, guarded, create_result)
                _generation_child(store, project_id, context, task_id, guarded,
                    lambda: store.begin_image_generation(project_id, result_node_id,
                        {"taskId": task_id, "count": 1, "providerId": route.provider_id,
                         "model": route.model, "prompt": prompt},
                        base_revision=store.canvas(project_id)["revision"]))
                started = True
            except BaseException as exc:
                exc.add_note(f"Generation operation {context.op_id} task {task_id}")
                try:
                    cleanup_start_error(exc)
                except BaseException as cleanup_error:
                    recovery_required = True
                    exc.add_note(f"Generation operation {context.op_id} task {task_id} cleanup failed; guard retained: {cleanup_error}")
                raise
        try:
            await run_blocking_to_completion(start)
        except BaseException as exc:
            try:
                await _generation_archive_to_completion(_generation_event(project_id, lifecycle, 'start'))
            except asyncio.CancelledError:
                pass
            if recovery_required:
                with CANVAS_TASK_LOCK:
                    CANVAS_TASKS[task_id] = {"id": task_id, "type": "agent-image", "status": "cleanup_pending",
                        "project_id": project_id, "node_id": node_id, "result_node_id": result_node_id,
                        "op_id": context.op_id, "created_at": time.time(), "updated_at": time.time(),
                        "result": None, "error": str(exc)}
                _track_lifetime_task(create_agent_ops_task(recover_start_error(exc)))
                if not getattr(exc, '__notes__', None):
                    exc.add_note(f"Generation operation {context.op_id} task {task_id} cleanup pending")
            elif isinstance(exc, asyncio.CancelledError) and started:
                with CANVAS_TASK_LOCK:
                    CANVAS_TASKS[task_id] = {"id": task_id, "type": "agent-image", "status": "queued",
                        "project_id": project_id, "node_id": node_id, "result_node_id": result_node_id,
                        "created_at": time.time(), "updated_at": time.time(), "result": None, "error": ""}
                _schedule_generation_worker(create_agent_ops_task, task_id, project_id, node_id, result_node_id,
                                            payload, context, guarded, lifecycle)
            else:
                await _generation_finish(store, project_id, task_id, guarded, lifecycle)
            raise
        try:
            await _generation_archive_to_completion(_generation_event(project_id, lifecycle, 'start'))
        except (Exception, asyncio.CancelledError) as exc:
            start_cancel = exc
    else:
        await _apply_mutations_from_snapshot(project_id, build_result)
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "agent-image",
            "status": "queued",
            "project_id": project_id,
            "node_id": node_id,
            "node_type": node_type,
            "result_node_id": result_node_id,
            "op_id": context.op_id if context else None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
        }
    if not context:
        try:
            rev = int(store.canvas(project_id).get("revision") or 0)
            await run_blocking_to_completion(
                store.begin_image_generation, project_id, result_node_id,
                {"taskId": task_id, "count": 1, "providerId": route.provider_id,
                 "model": route.model, "prompt": prompt}, base_revision=rev)
            await run_blocking_to_completion(store.flush, project_id)
        except (OSError, ProjectStoreError) as exc:
            from app.core import logger as _logger
            _logger.warning("begin_image_generation pending failed task=%s: %s", task_id, exc)

    if context:
        _schedule_generation_worker(create_agent_ops_task, task_id, project_id, node_id, result_node_id,
                                    payload, context, guarded, lifecycle)
    else:
        _track_lifetime_task(create_agent_ops_task(_run_agent_generation(task_id, project_id, node_id, result_node_id, payload)))
    if batch is not None:
        batch["tasks"].append({"node_id": node_id, "task_id": task_id})
    if context:
        await _broadcast_canvas_updated(project_id, source="agent")
    if start_cancel is not None:
        start_cancel.add_note(f"Generation operation {context.op_id} task {task_id}")
        raise start_cancel
    response = {"task_id": task_id, "type": "image", "node_id": node_id,
                "result_node_id": result_node_id, **_geometry(result_node)}
    if context:
        response["op_id"] = context.op_id
        response["touched_node_ids"] = _operation_touched(store, project_id, guarded | {context.op_id})
    return response


async def wait_for_task_core(project_id: str, task_id: str, timeout: int = 120) -> dict:
    """Wait for a generation task to complete (Request-free core; 1..600s)."""
    _validate_project(project_id)

    deadline = time.time() + timeout
    last_status = None

    while time.time() < deadline:
        try:
            status = await get_task_status(project_id, task_id)
        except HTTPException as e:
            if e.status_code == 404:
                # Task not found yet, keep waiting
                await asyncio.sleep(0.5)
                continue
            raise

        last_status = status
        if status["status"] in ("succeeded", "failed", "timeout", "cancelled"):
            return status

        # Poll with backoff
        await asyncio.sleep(min(1.0, (deadline - time.time()) / 2))

    # Timeout reached
    return {
        "task_id": task_id,
        "status": "timeout",
        "error": f"Wait timed out after {timeout}s (last status: {last_status.get('status', 'unknown') if last_status else 'not found'})",
        "last_status": last_status,
    }


async def _run_agent_generation(task_id: str, project_id: str, node_id: str,
                                result_node_id: str, payload: dict, context=None, guarded=None, lifecycle=None,
                                *, pre_cancel=False):
    """Run generation in the background and update task status.

    #351：成功后把图归档进项目媒体并挂到结果节点（complete_image_generation），
    写项目生成历史；失败走 fail_image_generation 清理 pendingTasks + 失败历史记录。
    """
    from app.services.agent_ops_async import run_blocking_to_completion
    started_at = _app_main.now_ms()
    store = _app_main.FIREFLY_PROJECT_STORE
    completed = False
    with CANVAS_TASK_LOCK:
        task_state = CANVAS_TASKS.get(task_id, {'id': task_id, 'status': 'cleanup_pending'})
    if context:
        lifecycle['worker_entered'] = True
    try:
        if pre_cancel:
            raise asyncio.CancelledError()
        if context:
            await run_blocking_to_completion(_generation_child, store, project_id, context, task_id, guarded,
                                             lambda: None, effects=("external image generation attempted",))
        result = await build_online_image_result(OnlineImageRequest(**payload))
        from app.services import archive_project_generated_image as archive
        items = []
        for idx, raw_item in enumerate(result.get("image_items") or []):
            try:
                if context:
                    await run_blocking_to_completion(_generation_child, store, project_id, context, task_id, guarded,
                                                     lambda: None, effects=("generated media archive attempted",))
                items.append(await _generation_archive_to_completion(archive(
                    project_id,
                    {"type": "url",
                     "value": str(raw_item.get("url") or ""),
                     "mime_type": str(raw_item.get("mime_type") or "image/png")},
                    idx)))
            except Exception as exc:  # noqa: BLE001
                from app.core import logger as _logger
                _logger.warning("#351 归档失败 task=%s idx=%s: %s", task_id, idx, exc)
        if context and not items:
            raise RuntimeError("Generation produced no archivable images")
        timestamp = _app_main.now_ms()
        history_record = {
            "id": f"generation-{uuid.uuid4().hex}",
            "task_id": task_id,
            "type": "online-image",
            "status": "succeeded",
            "prompt": payload.get("prompt", ""),
            "created_at": started_at,
            "createdAt": started_at,
            "completed_at": timestamp,
            "runMs": max(0, timestamp - started_at),
            "actual_provider_id": result.get("provider_id", ""),
            "actual_provider_name": result.get("provider_name", ""),
            "actual_model": result.get("model", ""),
            "model": result.get("model", ""),
            "params": payload.get("params") or {},
            "outputs": [{"asset_id": it["asset_id"], "kind": "image"} for it in items],
            "result_node_id": result_node_id,
            "source_node_id": node_id,
        }
        store = _app_main.FIREFLY_PROJECT_STORE
        # #375：目标结果卡可能已被用户 Ctrl+Z 撤掉。store.complete_*_generation 对
        # 缺失节点会在 (0,0) 新建幽灵卡；写回前先确认节点还在，不在就按「用户已
        # 丢弃结果」收场（cancelled + 明确 error），不把生成本身判死。
        if items and store.find_canvas_node(project_id, result_node_id) is None:
            with CANVAS_TASK_LOCK:
                task_state.update({
                    "status": "cleanup_pending" if context else "cancelled",
                    "_terminal_status": "cancelled",
                    "error": "目标结果卡已被移除，生成结果未写回",
                    "updated_at": time.time(),
                })
            return
        if items:
            def complete():
                nonlocal completed
                def apply():
                    nonlocal completed
                    canvas = store.complete_image_generation(project_id, result_node_id, items,
                        history_record, task_id=task_id, require_pending=True)
                    completed = True
                    return canvas
                if context:
                    return _generation_child(store, project_id, context, task_id, guarded, apply)
                canvas = apply()
                store.flush(project_id)
                return canvas
            canvas = await run_blocking_to_completion(complete)
            await _app_main.manager.broadcast_canvas_updated(
                project_id, int(canvas.get("updated_at") or timestamp), source="agent")
            result["result_node_id"] = result_node_id
            result["project_asset_ids"] = [it["asset_id"] for it in items]
            result["history_recorded"] = True
        with CANVAS_TASK_LOCK:
            task_state.update({
                "status": "cleanup_pending" if context else "succeeded",
                "_terminal_status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
    except (Exception, asyncio.CancelledError) as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        if cancelled and not context:
            raise
        if context and not completed:
            with store._lock:
                completed = any(h.get('task_id') == task_id and h.get('status') == 'succeeded'
                                for h in store._histories.get(project_id, []))
        if completed:
            result.update(result_node_id=result_node_id,
                          project_asset_ids=[it['asset_id'] for it in items], history_recorded=True)
            with CANVAS_TASK_LOCK:
                task_state.update(status="cleanup_pending", _terminal_status="succeeded",
                                             result=result, error="", updated_at=time.time())
            if cancelled:
                raise
            return
        failure_time = _app_main.now_ms()
        try:
            failure_record = {
                "id": f"generation-{uuid.uuid4().hex}",
                "task_id": task_id,
                "type": "online-image",
                "status": "failed",
                "prompt": payload.get("prompt", ""),
                "created_at": started_at,
                "createdAt": started_at,
                "completed_at": failure_time,
                "runMs": max(0, failure_time - started_at),
                "outputs": [],
                "result_node_id": result_node_id,
                "source_node_id": node_id,
                "error": str(exc),
            }
            def fail():
                def apply():
                    return store.fail_image_generation(project_id, result_node_id, task_id, failure_record,
                                                       require_pending=bool(context))
                if context:
                    return _generation_child(store, project_id, context, task_id, guarded, apply)
                canvas = apply()
                store.flush(project_id)
                return canvas
            if not completed:
                failed_canvas = await run_blocking_to_completion(fail)
                await _app_main.manager.broadcast_canvas_updated(
                    project_id, int(failed_canvas.get("updated_at") or failure_time), source="agent")
        except asyncio.CancelledError as inner:
            if not context:
                raise
            cancelled = True
            exc = inner
        except Exception as inner:  # noqa: BLE001
            from app.core import logger as _logger
            _logger.warning("#351 fail_image_generation cleanup failed task=%s: %s", task_id, inner)
        with CANVAS_TASK_LOCK:
            task_state.update({
                "status": "cleanup_pending" if context else ("cancelled" if cancelled else "failed"),
                "_terminal_status": "cancelled" if cancelled else "failed",
                "error": str(exc),
                "updated_at": time.time(),
            })
        if cancelled:
            raise exc
    finally:
        if context:
            async def settle():
                def cleanup():
                    store.flush(project_id)
                    with store._lock:
                        node = store.find_canvas_node(project_id, result_node_id) or {}
                        pending = any(item.get('taskId') == task_id for item in node.get('pendingTasks', [])
                                      if isinstance(item, dict))
                        if pending:
                            failure = {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id,
                                       'type': 'online-image', 'status': 'failed', 'prompt': payload.get('prompt', ''),
                                       'created_at': started_at, 'completed_at': _app_main.now_ms(), 'outputs': [],
                                       'result_node_id': result_node_id, 'source_node_id': node_id,
                                       'error': 'Generation cleanup required'}
                            _generation_child(store, project_id, context, task_id, guarded,
                                lambda: store.fail_image_generation(project_id, result_node_id, task_id,
                                                                    failure, require_pending=True))
                await run_blocking_to_completion(cleanup)
                return await _generation_finish(store, project_id, task_id, guarded, lifecycle)

            async def recover():
                from app.core import logger as _logger
                while True:
                    try:
                        if await settle():
                            with CANVAS_TASK_LOCK:
                                task_state.update(status=task_state.pop('_terminal_status', 'failed'), updated_at=time.time())
                            return
                        raise _JournalUnsettled('generation cleanup has unsettled journal entries')
                    except (OSError, sqlite3.Error, _JournalUnsettled) as error:
                        _logger.warning('generation cleanup retry task=%s op=%s: %s', task_id, context.op_id, error)
                        with CANVAS_TASK_LOCK:
                            task_state.update(error=f'Generation operation {context.op_id} cleanup pending: {error}',
                                                         updated_at=time.time())
                        await asyncio.sleep(1)
            async def finalize():
                try:
                    settled = await _generation_finish(store, project_id, task_id, guarded, lifecycle)
                    if not settled:
                        settled = await settle()
                except Exception:
                    # Keep undo guarded and hand off cleanup even if finalization raised after replacement.
                    settled = False
                if settled:
                    with CANVAS_TASK_LOCK:
                        task_state.update(status=task_state.pop('_terminal_status', 'failed'), updated_at=time.time())
                else:
                    from app.services.agent_ops_context import create_agent_ops_task
                    with CANVAS_TASK_LOCK:
                        task_state.setdefault('_terminal_status', task_state['status'])
                        task_state.update(status='cleanup_pending', error=f'Generation operation {context.op_id} cleanup pending',
                                    updated_at=time.time())
                    _track_lifetime_task(create_agent_ops_task(recover()))
            await _generation_archive_to_completion(finalize())


@generate_router.get("/tasks/{task_id}")
async def get_task_status(project_id: str, task_id: str, x_agent_project: str = Header(default="")):
    """Query the status of a generation task."""
    _validate_project(project_id)

    # Check in-memory task store first
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})

    if task:
        # Normalize the response
        result = task.get("result")
        return {
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "type": task.get("type", ""),
            "error": task.get("error", ""),
            "result": result,
            "op_id": task.get("op_id"),
            "updated_at": task.get("updated_at"),
        }

    raise agent_error(404, "TASK_NOT_FOUND", f"Task not found: {task_id}",
                      "任务不存在或已被清理；用 generate.trigger 重新发起并保存返回的 task_id。")


@generate_router.get("/tasks/{task_id}/wait")
async def wait_for_task(project_id: str, task_id: str, timeout: int = Query(default=120, ge=1, le=600), x_agent_project: str = Header(default="")):
    """Wait for a generation task to complete (with timeout)."""
    return await wait_for_task_core(project_id, task_id, timeout)


# =============================================================================
# Media tools
# =============================================================================


@media_router.post("/upload")
async def upload_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Upload a media file to the project library."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "media.upload", body,
                                  lambda: upload_media_core(project_id, body.get("file_path")))


async def upload_media_core(project_id: str, file_path: str) -> dict:
    """Import a local media file into the project library (Request-free core).

    #355：只允许读**当前项目目录**（store.root/<project_id>）
    与用户选定目录（存储设置三目录）下的图片；其他项目的目录也在禁区——
    跨项目偷读会被拒。越界与不存在返回同一句错误（不泄漏存在性）。
    """
    _validate_project(project_id)
    if not file_path:
        raise agent_error(400, "INVALID_ARGUMENT", "file_path is required",
                          "media.upload 需要本地文件路径 file_path。")

    store = _app_main.FIREFLY_PROJECT_STORE
    allowed_roots = []
    own_project_dir = None
    if getattr(store, "root", None):
        own_project_dir = Path(str(store.root)) / str(project_id)
    for root in (own_project_dir,
                 getattr(_app_main, "OUTPUT_INPUT_DIR", None),
                 getattr(_app_main, "OUTPUT_OUTPUT_DIR", None),
                 getattr(_app_main, "LOCAL_UPLOAD_DIR", None)):
        if not root:
            continue
        try:
            allowed_roots.append(Path(str(root)).resolve())
        except OSError:
            continue

    not_available = f"media file not available: {os.path.basename(str(file_path))}"
    try:
        source = Path(str(file_path)).resolve()
    except (OSError, ValueError):
        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                          "文件不存在或路径不可读；确认路径在允许目录内后重试。")
    if not any(source == root or source.is_relative_to(root) for root in allowed_roots):
        # 越界：与「不存在」同一句，不区分。
        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                          "路径不在允许目录内；只能导入当前项目目录或用户选定目录里的文件。")
    if not source.is_file():
        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                          "文件不存在；检查路径后重试。")
    mime = mimetypes.guess_type(source.name)[0] or ""
    if not mime.startswith("image/"):
        raise agent_error(400, "INVALID_ARGUMENT",
                          "only image files can be uploaded via media.upload",
                          "只能导入图片；其它文件类型请让用户在界面里操作。")

    context = _media_context(project_id)
    if context and not 0 < source.stat().st_size <= 1024 * 1024 * 1024:
        raise agent_error(400, "INVALID_ARGUMENT", "Media file is empty or exceeds 1GB")
    source_info = {"locator": str(source), "name": source.name, "type": "local_output"}
    guarded = set()
    lifecycle = None
    try:
        if context:
            import hashlib
            import copy
            digest = hashlib.sha256()
            path_initial = source.stat()
            with source.open("rb") as handle:
                initial = os.fstat(handle.fileno())
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                with store._lock:
                    # Windows can report different ctime values for a handle and a path.
                    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                    compatible = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                    same_file = os.path.samefile(handle.fileno(), source)
                    resolved = source.resolve()
                    if resolved != source:
                        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                                          "文件不存在或路径不可读；确认路径在允许目录内后重试。")
                    current = source.stat()
                    final = os.fstat(handle.fileno())
                    index = store._load_media_index(project_id)
                    existing = index["items"].get(digest.hexdigest())
                    stable_initial = (same_file and signature(initial) == signature(final)
                                      and signature(path_initial) == signature(current)
                                      and compatible(final) == compatible(current))
                    if not stable_initial or not _indexed_media_exists(store, project_id, existing):
                        existing = None
                    if existing and not store._merge_media_source(copy.deepcopy(existing), source_info):
                        return {"success": True, "asset_id": existing["asset_id"], "media": existing}
                handle.close()
            lifecycle = await _media_start(project_id, context, [])
            from app.services.agent_ops_async import run_blocking_to_completion
            def import_checked():
                with store._lock:
                    if source.resolve() != source:
                        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                                          "文件不存在或路径不可读；确认路径在允许目录内后重试。")
                    # Recheck under the import lock: another importer can win while start is awaited.
                    current_digest = hashlib.sha256()
                    with source.open('rb') as current_handle:
                        initial_stat = os.fstat(current_handle.fileno())
                        for chunk in iter(lambda: current_handle.read(1024 * 1024), b''):
                            current_digest.update(chunk)
                        final_stat = os.fstat(current_handle.fileno())
                        path_stat = source.stat()
                        stable = (signature(initial_stat) == signature(final_stat)
                                  and compatible(final_stat) == compatible(path_stat)
                                  and os.path.samefile(current_handle.fileno(), source)
                                  and source.resolve() == source)
                    if source.resolve() != source:
                        raise agent_error(404, "ASSET_NOT_FOUND", not_available,
                                          "文件不存在或路径不可读；确认路径在允许目录内后重试。")
                    existing = store._load_media_index(project_id)['items'].get(current_digest.hexdigest())
                    if (stable_initial and stable and _indexed_media_exists(store, project_id, existing)
                            and not store._merge_media_source(copy.deepcopy(existing), source_info)):
                        return existing
                    _media_root(store, project_id, context,
                                "media import attempted; file or source metadata may have changed and cannot be undone", guarded)
                return store.import_media_file(project_id, str(source), source.name, "", source_info=source_info)
            record = await run_blocking_to_completion(import_checked)
        else:
            record = store.import_media_file(project_id, str(source), source.name, "", source_info=source_info)
    except Exception as e:
        if guarded:
            raise _media_partial(context, e) from e
        if isinstance(e, HTTPException):
            raise
        raise agent_error(500, "INTERNAL_ERROR", str(e),
                          "导入失败；检查文件后重试，反复失败请报告用户。") from e
    finally:
        try:
            if lifecycle:
                await _media_end(project_id, *lifecycle)
        finally:
            if guarded:
                _generation_release(store, project_id, context.op_id, guarded)

    result = {"success": True, "asset_id": record["asset_id"], "media": record}
    if context and guarded:
        result.update(op_id=context.op_id, touched_node_ids=[])
    return result


@media_router.get("/list")
async def list_media(project_id: str, x_agent_project: str = Header(default="")):
    """List all media assets in the project."""
    _validate_project(project_id)

    # Use the existing list endpoint
    from app.routers.api_project_library import list_project_media

    response = await list_project_media(project_id)
    return response


@media_router.delete("/{asset_id}")
async def delete_media(project_id: str, asset_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Delete a media file from the project. #357：agent 调用经服务端确认门。"""
    _validate_project(project_id)
    await _server_confirm_gate(request, project_id, "media.delete",
                               {"asset_id": asset_id}, f"删除媒体资源 {asset_id}")
    return await _rest_agent_call(request, project_id, "media.delete", {"asset_id": asset_id},
                                  lambda: delete_media_core(project_id, asset_id), agent_node_id)


async def delete_media_core(project_id: str, asset_id: str) -> dict:
    """#357：删媒体执行核（无门；REST/MCP 各自门过后调）。"""
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    context = _media_context(project_id)
    guarded = set()
    lifecycle = None
    try:
        if context:
            import re
            if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
                raise agent_error(400, "INVALID_ARGUMENT", "Invalid asset ID")
            with store._lock:
                index = store._load_media_index(project_id)
                if asset_id not in index["items"]:
                    return {"success": True, "asset_id": asset_id}
                _media_root(store, project_id, context,
                            f"media deletion attempted for {asset_id}; file and index may be irreversibly removed", guarded)
            lifecycle = await _media_start(project_id, context, [])
            from app.services.agent_ops_async import run_blocking_to_completion
            await run_blocking_to_completion(store.delete_media, project_id, asset_id)
        else:
            store.delete_media(project_id, asset_id)
    except Exception as e:
        if guarded:
            raise _media_partial(context, e) from e
        if isinstance(e, HTTPException):
            raise
        raise agent_error(500, "INTERNAL_ERROR", str(e),
                          "删除失败；先调用 media.list 确认资产还在，再重试一次。") from e
    finally:
        try:
            if lifecycle:
                await _media_end(project_id, *lifecycle)
        finally:
            if guarded:
                _generation_release(store, project_id, context.op_id, guarded)
    result = {"success": True, "asset_id": asset_id}
    if context:
        result.update(op_id=context.op_id, touched_node_ids=[])
    return result


# =============================================================================
# Viewport tools
# =============================================================================


@viewport_router.post("/focus")
async def focus_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Scroll canvas to focus on a node."""
    body = await request.json()
    return await focus_node_core(project_id, body.get("node_id"))


@viewport_router.post("/zoom")
async def set_zoom(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Set the canvas zoom level."""
    body = await request.json()
    return await set_zoom_core(project_id, body.get("level"))


async def focus_node_core(project_id: str, node_id: str) -> dict:
    """Focus the canvas viewport on a node (Request-free core).

    #243: besides updating the server-side viewport state, the command is
    dispatched TARGETED to the single tab that has the project open (not
    broadcast, ADR 0022). Frontend consumption of `canvas_viewport_command`
    is a follow-up (smart-canvas.js is in a separate queue).
    """
    _validate_project(project_id)
    if not node_id:
        raise agent_error(400, "INVALID_ARGUMENT", "node_id is required",
                          "先调用 canvas.list_nodes 获取节点 ID。")

    # Get the node's position
    target_node = _find_node(project_id, node_id)

    # Store the viewport state
    x = target_node.get("x", 0)
    y = target_node.get("y", 0)
    VIEWPORT_STATE[project_id] = {
        "focus_node_id": node_id,
        "x": x,
        "y": y,
        "scale": 1.0,
        "updated_at": time.time(),
    }

    dispatched = await _dispatch_viewport_command(project_id, {"command": "focus", "node_id": node_id, "x": x, "y": y})
    return {"success": True, "node_id": node_id, "dispatched": dispatched}


async def set_zoom_core(project_id: str, level) -> dict:
    """Set the canvas zoom level (Request-free core). See focus_node_core."""
    _validate_project(project_id)
    if level is None:
        raise agent_error(400, "INVALID_ARGUMENT", "level is required",
                          "viewport.set_zoom 需要数字 level（1.0 = 100%）。")

    # Store the viewport state
    current = VIEWPORT_STATE.get(project_id, {})
    VIEWPORT_STATE[project_id] = {
        **current,
        "scale": float(level),
        "updated_at": time.time(),
    }

    dispatched = await _dispatch_viewport_command(project_id, {"command": "zoom", "level": float(level)})
    return {"success": True, "level": level, "dispatched": dispatched}


@viewport_router.get("/state")
async def get_viewport_state(project_id: str, x_agent_project: str = Header(default="")):
    """Get the current viewport state."""
    _validate_project(project_id)
    return VIEWPORT_STATE.get(project_id, {"scale": 1.0, "x": 0, "y": 0})


# =============================================================================
# Confirmation gate
# =============================================================================


@confirm_router.post("/confirm")
async def request_confirmation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Request user confirmation for a destructive operation.

    Called by the canvas-tools extension before executing destructive tools.
    Sends a confirm event to the frontend via the agent's WS stream and blocks
    waiting for the user's decision (with timeout).

    For nodes with autonomy="autonomous", destructive tools are auto-approved
    (except safety-floor tools which always require confirmation).

    Body: {"node_id": "...", "tool": "...", "params": {...}, "message": "..."}
    Returns: {"approved": true} or {"approved": false, "reason": "timeout"|"rejected"}
    """
    _validate_project(project_id)
    body = await request.json()
    return await request_confirmation_core(
        project_id,
        body.get("node_id"),
        body.get("tool", ""),
        body.get("params", {}),
        body.get("message", ""),
        body.get("timeout_seconds", 60),
    )


async def request_confirmation_core(
    project_id: str,
    node_id: str,
    tool: str,
    params: dict,
    message: str,
    timeout_seconds: float = 60,
) -> dict:
    """Confirmation gate core (Request-free; shared by HTTP + MCP, #243).

    Blocks until the user confirms/rejects on the canvas or times out.
    Returns {"approved": bool, "reason": "rejected" | "timeout" | None}.
    """
    if not node_id:
        raise agent_error(400, "CONFIRM_MISSING_NODE_ID", "node_id is required",
                          "确认门需要发起节点：调用时带 ?agent_node_id=<节点>。")

    from app.services.agent_host import agent_host_manager, SAFETY_FLOOR_TOOLS

    # Get the agent session
    session = await agent_host_manager.get_or_create_session(project_id, node_id)

    # Check autonomy level: auto-approve destructive tools in autonomous mode
    # (except safety-floor tools which always require confirmation)
    autonomy = session.autonomy or "cautious"
    if autonomy == "autonomous" and tool not in SAFETY_FLOOR_TOOLS:
        return {"approved": True}

    # Generate a unique confirm ID
    confirm_id = f"confirm-{uuid.uuid4().hex[:12]}"

    # Create a completion event and result holder
    loop = asyncio.get_event_loop()
    done_event = asyncio.Event()
    result = {"approved": False, "reason": "timeout"}

    # Register a temporary confirm handler
    async def handle_confirm_response(cid, approved):
        if cid == confirm_id:
            result["approved"] = approved
            result["reason"] = "rejected" if not approved else None
            done_event.set()

    agent_host_manager.on_confirm(session, handle_confirm_response)

    try:
        # Send the confirm event to the frontend via the session's event handlers
        confirm_event = {
            "type": "confirm",
            "confirm_id": confirm_id,
            "tool": tool,
            "params": params,
            "message": message,
        }
        # #389：先写入回放日志（就地注入 seq，live WS 与回放同一 payload，同 #318 票 C
        # 单一 fan-out 口径）——确认挂起期间刷新页面才能从 events 回放重建待决确认卡。
        agent_host_manager._persist_event(session, confirm_event)
        for handler in session.event_handlers:
            try:
                await handler(session, confirm_event)
            except Exception:
                pass

        # Wait for the user's decision (with timeout)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            result["approved"] = False
            result["reason"] = "timeout"

        # #389：门结束（批准/拒绝/超时）落终态记录并 live 广播：回放时前端据此关闭
        # 已决确认卡（「确认结束后不再重放」），live 端同步关掉挂着的 ffDialog。
        resolved_event = {
            "type": "confirm_resolved",
            "confirm_id": confirm_id,
            "tool": tool,
            "approved": bool(result["approved"]),
            "reason": result["reason"],
        }
        agent_host_manager._persist_event(session, resolved_event)
        for handler in session.event_handlers:
            try:
                await handler(session, resolved_event)
            except Exception:
                pass

        return {"approved": result["approved"], "reason": result["reason"]}
    finally:
        # Clean up the temporary handler
        if handle_confirm_response in session.confirm_handlers:
            session.confirm_handlers.remove(handle_confirm_response)


# =============================================================================
# Canvas view tools (#266) — read-only; ADR 0022 双面上线（REST 端点 + MCP TOOLS）。
# 慢操作独立 90s 超时契约由 api_mcp.SLOW_TOOL_TIMEOUTS 施加（ComfyArt SLOW_S=90s 模式）。
# =============================================================================


def _load_image_or_none(path: str):
    """Open a local media file as an Image, or None when unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.copy()
    except Exception:
        return None


def _video_frame_or_none(path: str, width: int):
    """Extract one video preview frame (ffmpeg), or None when unavailable."""
    try:
        from app.services.generation import generate_video_preview_image

        return generate_video_preview_image(path, width)
    except Exception:
        return None


def _find_node_for_view(project_id: str, node_id: str) -> dict:
    """_find_node with the #266 error-text contract (next-step hint)."""
    if not node_id:
        raise agent_error(400, "INVALID_ARGUMENT",
                          "node_id 必填",
                          "先调用 canvas.list_nodes 获取节点 ID。")
    try:
        return _find_node(project_id, node_id)
    except HTTPException:
        raise agent_error(404, "NODE_NOT_FOUND", f"节点 {node_id} 不存在",
                          "先调用 canvas.list_nodes 查看画布状态。") from None


async def canvas_view_core(project_id: str, scope: str = "page", node_ids=None, max_px=None) -> dict:
    """#266 canvas.view: 整页/选区导出为一张 contact sheet（base64 PNG）喂模型（只读）。

    ComfyArt §59.4 契约：max_px = min(2048, args.max_px || 1024)；视频资产用单帧
    预览图代表（无 ffmpeg 时摘除）；错误文案带下一步建议（票面规范）。
    """
    from app.services.canvas_view import clamp_view_max_px, compose_contact_sheet

    scope = str(scope or "page").strip().lower()
    if scope not in ("page", "selection"):
        raise agent_error(400, "INVALID_ARGUMENT", "scope 必须是 page 或 selection",
                          "先调用 canvas.list_nodes 获取节点 ID，选区传 scope=selection + node_ids。")
    px = clamp_view_max_px(max_px)
    canvas = _open_canvas(project_id)
    nodes = [n for n in canvas.get("nodes", []) if isinstance(n, dict)]
    if scope == "selection":
        wanted = [str(x) for x in (node_ids or []) if str(x).strip()]
        if not wanted:
            raise agent_error(400, "INVALID_ARGUMENT", "selection 作用域需要 node_ids",
                              "先调用 canvas.list_nodes 获取节点 ID。")
        by_id = {str(n.get("id", "")): n for n in nodes}
        selected = []
        for nid in wanted:
            node = by_id.get(nid)
            if node is None:
                raise agent_error(404, "NODE_NOT_FOUND", f"节点 {nid} 不存在",
                                  "先调用 canvas.list_nodes 查看画布状态。")
            selected.append(node)
    else:
        selected = nodes
    store = _app_main.FIREFLY_PROJECT_STORE
    items = []
    for node in selected:
        for image in node.get("images") or []:
            if not isinstance(image, dict):
                continue
            asset_id = str(image.get("asset_id") or "")
            if not asset_id:
                continue
            kind = str(image.get("kind") or "")
            try:
                record = await asyncio.to_thread(store.media_record, project_id, asset_id)
            except Exception:
                continue  # dangling asset 引用——跳过，不整单失败
            path = str(record.get("path") or "")
            if not path:
                continue
            if kind == "video":
                tile = await asyncio.to_thread(_video_frame_or_none, path, 256)
                if tile is None:
                    continue  # 无 ffmpeg/不可读——可用 canvas.view_media 查看 ffmpeg 下一步提示
                tile_kind = kind
            else:
                tile = await asyncio.to_thread(_load_image_or_none, path)
                if tile is None:
                    continue
                tile_kind = "image"
            items.append({
                "node_id": str(node.get("id", "")),
                "asset_id": asset_id,
                "kind": tile_kind,
                "name": str(image.get("name") or record.get("name") or ""),
                "tile": tile,
            })
    if not items:
        if scope == "selection":
            raise agent_error(404, "ASSET_NOT_FOUND", "选区节点没有可查看的媒体",
                              "先调用 canvas.list_nodes 查看节点媒体，或先调用 media.upload 导入图片。")
        raise agent_error(400, "ASSET_NOT_FOUND", "画布上没有可查看的媒体节点",
                          "先调用 media.upload 导入图片，或先调用 canvas.list_nodes 查看节点状态。")
    sheet = await asyncio.to_thread(compose_contact_sheet, [(it["node_id"], it.pop("tile")) for it in items], px)
    import base64

    from local_edit import encode_png

    data_url = "data:image/png;base64," + base64.b64encode(encode_png(sheet)).decode("ascii")
    return {
        "image_data_url": data_url,
        "width": sheet.width,
        "height": sheet.height,
        "max_px": px,
        "node_count": len({it["node_id"] for it in items}),
        "items": items,
    }


async def view_media_core(project_id: str, node_id, max_px=None, frames=None) -> dict:
    """#266 canvas.view_media: 查看单节点媒体（图像下缩放 / 视频抽帧拼图，只读）。

    视频抽帧复用 generate_video_preview_image 链路（均匀采样，contact sheet 拼贴）；
    全部帧失败时报错带 ffmpeg 下一步建议（票面规范）。
    """
    from app.services.canvas_view import (
        MAX_VIDEO_FRAMES,
        clamp_view_max_px,
        fit_tile,
        video_contact_sheet,
        video_duration_seconds,
        video_frame_times,
    )

    node = _find_node_for_view(project_id, str(node_id or ""))
    store = _app_main.FIREFLY_PROJECT_STORE
    px = clamp_view_max_px(max_px)
    try:
        n_frames = max(1, min(MAX_VIDEO_FRAMES, int(frames or 4)))
    except (TypeError, ValueError):
        n_frames = 4
    images = [it for it in (node.get("images") or []) if isinstance(it, dict) and str(it.get("asset_id") or "")]
    if not images:
        raise agent_error(404, "ASSET_NOT_FOUND", f"节点 {node_id} 没有可查看的媒体",
                          "先调用 media.upload 导入图片，或先调用 canvas.list_nodes 查看节点状态。")
    image_items = [it for it in images if str(it.get("kind") or "") != "video"]
    picked = image_items[0] if image_items else images[0]
    is_video = str(picked.get("kind") or "") == "video"
    try:
        record = await asyncio.to_thread(store.media_record, project_id, str(picked["asset_id"]))
    except Exception:
        raise agent_error(404, "ASSET_NOT_FOUND", f"节点 {node_id} 的媒体资源不存在",
                          "先调用 media.list 查看资源库。") from None
    path = str(record.get("path") or "")
    if is_video:
        from app.services.generation import generate_video_preview_image

        duration = await asyncio.to_thread(video_duration_seconds, path)
        sheet = await asyncio.to_thread(video_contact_sheet, path, generate_video_preview_image, duration, n_frames, max(256, px // 2), px)
        if sheet is None:
            raise agent_error(400, "MEDIA_UNREADABLE", f"视频抽帧失败（节点 {node_id}）",
                              "请检查服务器已安装 ffmpeg 且媒体文件可播放，然后重试；也可先调用 canvas.list_nodes 查看节点状态。")
        kind, frames_used = "video", len(video_frame_times(duration, n_frames))
    else:
        src = await asyncio.to_thread(_load_image_or_none, path)
        if src is None:
            raise agent_error(400, "MEDIA_UNREADABLE", f"节点 {node_id} 的媒体无法读取",
                              "请检查媒体文件是否损坏，或先调用 canvas.list_nodes 查看节点状态。")
        sheet, kind, frames_used = fit_tile(src, px, px), "image", 1
    import base64

    from local_edit import encode_png

    data_url = "data:image/png;base64," + base64.b64encode(encode_png(sheet)).decode("ascii")
    return {
        "kind": kind,
        "image_data_url": data_url,
        "width": sheet.width,
        "height": sheet.height,
        "frames": frames_used,
        "asset_id": str(picked["asset_id"]),
        "name": str(picked.get("name") or record.get("name") or ""),
    }


BATCH_EDIT_MAX_NODES = 10


async def _load_node_image(project_id: str, node_id: str, picked: dict):
    """#266 edit_* 共享：从资源库加载节点选中图像；404/400 错误文案带下一步建议（票面规范）。"""
    store = _app_main.FIREFLY_PROJECT_STORE
    try:
        record = await asyncio.to_thread(store.media_record, project_id, str(picked["asset_id"]))
    except Exception:
        raise agent_error(404, "ASSET_NOT_FOUND", f"节点 {node_id} 的媒体资源不存在",
                          "先调用 media.list 查看资源库。") from None
    path = str(record.get("path") or "")
    source = await asyncio.to_thread(_load_image_or_none, path)
    if source is None:
        raise agent_error(400, "MEDIA_UNREADABLE", f"节点 {node_id} 的图像无法读取",
                          "请检查媒体文件是否损坏后重试。")
    return source


async def _replace_node_image(project_id: str, node: dict, node_id: str, picked: dict, edited, filename: str, extra_node_fields: dict | None = None) -> dict:
    context = _media_context(project_id)
    if not context or _MEDIA_LIFECYCLE.get() is not None:
        return await _replace_node_image_impl(project_id, node, node_id, picked, edited, filename, extra_node_fields)
    store = _app_main.FIREFLY_PROJECT_STORE
    token = f'media-lifecycle:{context.op_id}'
    set_agent_generation_running(store, project_id, context.op_id, token, True)
    lifecycle = None
    try:
        lifecycle = await _media_start(project_id, context, [node_id])
        return await _replace_node_image_impl(project_id, node, node_id, picked, edited, filename, extra_node_fields)
    finally:
        try:
            if lifecycle:
                await _media_end(project_id, *lifecycle)
        finally:
            set_agent_generation_running(store, project_id, context.op_id, token, False)


async def _replace_node_image_impl(project_id: str, node: dict, node_id: str, picked: dict, edited, filename: str, extra_node_fields: dict | None = None) -> dict:
    """#266 edit_* 共享写回：新资源入资源库（原资源保留可恢复）+ 覆写节点图，返回结果摘要。

    extra_node_fields：可选的附加节点字段（如 #289 的 node.outpaintSize），与 images 覆写同一次 upsert 落盘。
    """
    from local_edit import encode_png

    context = _media_context(project_id)
    transforms = {"canvas.edit_crop", "canvas.edit_rotate", "canvas.edit_flip", "canvas.edit_outpaint"}
    if context and context.tool not in transforms | {"canvas.edit_erase"}:
        _require_media_journal_support()
    transform = bool(context and context.tool in transforms)
    store = _app_main.FIREFLY_PROJECT_STORE
    guarded = set()
    from app.services.agent_ops_async import run_blocking_to_completion
    try:
        if context:
            content = encode_png(edited)
            def archive():
                import hashlib
                digest = hashlib.sha256(content).hexdigest()
                with store._lock:
                    existing = store._load_media_index(project_id)["items"].get(digest)
                    exists = _indexed_media_exists(store, project_id, existing)
                    if exists:
                        return existing
                    if transform:
                        _media_root(store, project_id, context,
                                    "edited media import attempted; archive may have changed and cannot be undone", guarded)
                    if not transform:
                        _generation_child(store, project_id, context, context.op_id, guarded, lambda: None,
                                          effects=("edited media import attempted; archive may have changed and cannot be undone",))
                    return store.import_media(project_id, content, filename, "image/png")
            asset = await run_blocking_to_completion(archive)
        else:
            asset = await asyncio.to_thread(store.import_media, project_id, encode_png(edited), filename, "image/png")
        new_asset_id = str(asset["asset_id"])
        updated_at = _app_main.now_ms()
    except BaseException as exc:
        if context:
            _generation_release(store, project_id, context.op_id, guarded)
        if transform and guarded:
            raise _media_partial(context, exc) from exc
        raise
    def build(canvas):
        # Re-read even on the first attempt: encoding/import may have yielded to
        # a human edit or generation completion since the image was selected.
        current = _snapshot_node(canvas, node_id)
        images = [dict(it) for it in (current.get("images") or []) if isinstance(it, dict)]
        if context and _node_image_for_edit(current, node_id) != picked:
            raise agent_error(409, "CONFLICT", "Node image changed during edit",
                              "检查画布和操作日志后再决定是否重试。")
        replaced = False
        for it in images:
            if str(it.get("asset_id") or "") == str(picked["asset_id"]):
                it.update(asset_id=new_asset_id, kind="image", name=filename)
                replaced = True
                break
        if not replaced:
            images.insert(0, {"asset_id": new_asset_id, "kind": "image", "name": filename})
        patched = {**current, **(extra_node_fields or {}), "images": images, "updated_at": updated_at}
        if current.get("output_asset_id"):
            patched["output_asset_id"] = new_asset_id
        return [{"op": "upsert_node", "node": patched}]

    try:
        if context:
            def update():
                canvas = store.canvas(project_id)
                return store.apply_mutations(project_id, build(canvas), base_revision=canvas["revision"])
            mutation = _generation_mutation if transform and not guarded else _generation_child
            await run_blocking_to_completion(mutation, store, project_id, context, context.op_id, guarded, update)
            await _broadcast_canvas_updated(project_id, source="agent")
        else:
            await _apply_mutations_from_snapshot(project_id, build)
    except BaseException as exc:
        if transform and guarded:
            raise _media_partial(context, exc) from exc
        raise
    finally:
        if context:
            _generation_release(store, project_id, context.op_id, guarded)
    result = {
        "success": True,
        "node_id": node_id,
        "asset_id": new_asset_id,
        "original_asset_id": str(picked["asset_id"]),
        "width": edited.width,
        "height": edited.height,
    }
    if context:
        result["touched_node_ids"] = _operation_touched(store, project_id, guarded | {context.op_id})
        if transform and guarded:
            result["op_id"] = context.op_id
    return result


async def batch_edit_core(project_id: str, node_ids, prompt, agent_node_id: str = "") -> dict:
    """Write the prompt and start one generation per node."""
    _validate_project(project_id)
    if not isinstance(node_ids, list) or not node_ids or not all(isinstance(n, str) and n for n in node_ids):
        raise agent_error(400, "INVALID_ARGUMENT", "node_ids 必须是非空节点 ID 数组",
                          "先调用 canvas.list_nodes 获取节点 ID。")
    if len(node_ids) > BATCH_EDIT_MAX_NODES:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"node_ids 单次最多 {BATCH_EDIT_MAX_NODES} 个节点（本次 {len(node_ids)}）",
                          "请拆成多批重试。")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise agent_error(400, "INVALID_ARGUMENT", "prompt 必须是非空字符串",
                          "请填写批量 prompt 后重试。")
    from app.services.agent_ops_context import current_agent_ops_context
    from app.services.agent_ops_async import run_blocking_to_completion
    context = current_agent_ops_context()
    if context and (context.project_id != project_id or not context.agent_node_id or
                    context.tool != "canvas.batch_edit" or context.agent_node_id != agent_node_id):
        raise agent_error(400, "INVALID_ARGUMENT", "Matching batch caller identity is required")
    tasks = []
    ownership_fields(agent_node_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    # The scheduling token closes the gap between child tasks; each worker adds
    # its own token before the scheduling token can be released.
    batch = {"root": context.op_id, "root_exists": False, "ids": set(),
             "tokens": {context.op_id}, "tasks": tasks, "unregistered": {}} if context else None
    token = _BATCH_GENERATION.set(batch)
    guarded = set()
    try:
        for node_id in node_ids:
            updated_at = _app_main.now_ms()

            def build(canvas):
                node = _snapshot_node(canvas, node_id)
                if context and node.get("prompt") == prompt:
                    return []
                return [{"op": "upsert_node", "node": {**node, "prompt": prompt, "updated_at": updated_at}}]

            if context:
                from contextlib import nullcontext
                from app.services.agent_ops_context import agent_ops_scope
                child = batch['root_exists']
                scope = (agent_ops_scope(project_id=project_id, agent_node_id=context.agent_node_id,
                                         turn_id=context.turn_id, tool=context.tool, args=context.args)
                         if child else nullcontext(context))
                with scope as source_context:
                    lifecycle = await _media_start(project_id, source_context, [node_id])
                    try:
                        def update():
                            def apply():
                                canvas = store.canvas(project_id)
                                mutations = build(canvas)
                                if mutations:
                                    return store.apply_mutations(project_id, mutations, base_revision=canvas["revision"])
                            return _generation_mutation(store, project_id, source_context, context.op_id,
                                                        guarded, apply,
                                                        parent_id=context.op_id if child else None)
                        await run_blocking_to_completion(update)
                    finally:
                        await _media_end(project_id, *lifecycle)
            else:
                await _apply_mutations_from_snapshot(project_id, build)
            triggered = await trigger_generation_core(project_id, node_id, agent_node_id=agent_node_id)
            if not context:
                tasks.append({"node_id": node_id, "task_id": str(triggered.get("task_id") or "")})
        result = {"success": True, "count": len(tasks), "prompt": prompt, "tasks": tasks}
        if context and batch["root_exists"]:
            result["op_id"] = context.op_id
            result["touched_node_ids"] = _operation_touched(store, project_id, batch['ids'])
        return result
    except Exception as exc:
        if context and batch["root_exists"]:
            # The batch may already have started paid tasks; never suggest a blind retry.
            from app.services.agent_errors import translate_detail
            status = exc.status_code if isinstance(exc, HTTPException) else 500
            detail = translate_detail(status, exc.detail if isinstance(exc, HTTPException) else str(exc))
            detail["details"] = {**detail.get("details", {}), "op_id": context.op_id,
                                 "tasks": tasks, "status": "partial-unknown"}
            detail["retryable"] = False
            raise HTTPException(status_code=status, detail=detail) from exc
        raise
    finally:
        if context and not _generation_release(store, project_id, context.op_id, guarded):
            from app.services.agent_ops_context import create_agent_ops_task
            from app.core import logger as _logger

            journal = _journal_for(store)

            def retry_registration():
                with store._lock:
                    for op_id, (record, delta) in list(batch['unregistered'].items()):
                        _batch_register_reserved(journal, project_id, record, delta)
                        batch['unregistered'].pop(op_id)
                        store._agent_generation_unregistered.discard((project_id, op_id))
                    store.flush(project_id)

            async def recover_batch_journal():
                while True:
                    try:
                        await run_blocking_to_completion(retry_registration)
                        if _generation_release(store, project_id, context.op_id, guarded):
                            return
                    except (OSError, sqlite3.Error) as exc:
                        _logger.warning("batch journal retry op=%s: %s", context.op_id, exc)
                    await asyncio.sleep(1)

            _track_lifetime_task(create_agent_ops_task(recover_batch_journal()))
        _BATCH_GENERATION.reset(token)


def _node_image_for_edit(node: dict, node_id: str) -> dict:
    """Pick the node's first image-kind media entry, or raise 400 with a next-step hint."""
    images = [it for it in (node.get("images") or [])
              if isinstance(it, dict) and str(it.get("asset_id") or "") and str(it.get("kind") or "") != "video"]
    if not images:
        raise agent_error(400, "ASSET_NOT_FOUND", f"节点 {node_id} 没有可编辑的图像",
                          "先调用 canvas.list_nodes 查看节点媒体，或先调用 media.upload 导入图片。")
    return images[0]


async def crop_node_image_core(project_id: str, node_id, rect) -> dict:
    """#266 canvas.edit_crop：本地裁节点图选区（纯 PIL，不消耗外部生成）并覆写节点图。

    rect 为原图像素坐标 {x, y, width, height}；新资源入资源库（原资源保留）。
    写操作：MCP 面施加 #242 确认门（CONFIRMATION_GATED_TOOLS）；错误文案带下一步建议。
    """
    import time as _time

    from local_edit import crop_rect, encode_png, validate_rect

    node_id = str(node_id or "")
    node = _find_node_for_view(project_id, node_id)
    if not isinstance(rect, dict):
        raise agent_error(400, "INVALID_ARGUMENT",
                          "rect 必须是 {x, y, width, height}（原图像素坐标）",
                          "先调用 canvas.get_node 查看节点媒体。")
    try:
        x = float(rect.get("x") or 0)
        y = float(rect.get("y") or 0)
        w = float(rect.get("width") or 0)
        h = float(rect.get("height") or 0)
    except (TypeError, ValueError):
        raise agent_error(400, "INVALID_ARGUMENT", "rect 参数必须是数字",
                          "请检查 x/y/width/height 后重试。") from None
    picked = _node_image_for_edit(node, node_id)
    source = await _load_node_image(project_id, node_id, picked)
    try:
        left, top, right, bottom = validate_rect([x, y, x + w, y + h], source.size)
    except ValueError:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"选区越界（图像 {source.width}x{source.height}）",
                          "先调用 canvas.get_node 查看节点媒体尺寸，再检查 rect 的 x/y/width/height 后重试。") from None
    cropped = await asyncio.to_thread(crop_rect, source, [left, top, right, bottom])
    filename = f"edit-crop-{int(_time.time() * 1000)}.png"
    result = await _replace_node_image(project_id, node, node_id, picked, cropped, filename)
    result["rect"] = [left, top, right, bottom]
    return result


async def rotate_node_image_core(project_id: str, node_id, angle) -> dict:
    """#266b canvas.edit_rotate：本地旋转节点图（90/180/270 逆时针，纯 PIL，不消耗外部生成）并覆写节点图。

    新资源入资源库（原资源保留可恢复）。写操作：MCP 面施加 #242 确认门（CONFIRMATION_GATED_TOOLS）；
    错误文案带下一步建议（票面规范）。
    """
    from local_edit import rotate_image

    node_id = str(node_id or "")
    node = _find_node_for_view(project_id, node_id)
    try:
        angle = int(angle)
    except (TypeError, ValueError):
        raise agent_error(400, "INVALID_ARGUMENT",
                          "angle 必须是 90/180/270 之一（逆时针）",
                          "先调用 canvas.get_node 查看节点媒体。") from None
    if angle not in (90, 180, 270):
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"angle 必须是 90/180/270 之一（本次 {angle}）",
                          "先调用 canvas.get_node 查看节点媒体。")
    picked = _node_image_for_edit(node, node_id)
    source = await _load_node_image(project_id, node_id, picked)
    edited = await asyncio.to_thread(rotate_image, source, angle)
    filename = f"edit-rotate-{int(time.time() * 1000)}.png"
    result = await _replace_node_image(project_id, node, node_id, picked, edited, filename)
    result["angle"] = angle
    return result


async def flip_node_image_core(project_id: str, node_id, direction) -> dict:
    """#266b canvas.edit_flip：本地镜像节点图（horizontal 左右 / vertical 上下，纯 PIL，不消耗外部生成）并覆写节点图。

    新资源入资源库（原资源保留可恢复）。写操作：MCP 面施加 #242 确认门（CONFIRMATION_GATED_TOOLS）；
    错误文案带下一步建议（票面规范）。
    """
    from local_edit import flip_image

    node_id = str(node_id or "")
    node = _find_node_for_view(project_id, node_id)
    direction = str(direction or "").strip().lower()
    if direction not in ("horizontal", "vertical"):
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"direction 必须是 horizontal（左右）或 vertical（上下）之一（本次 {direction!r}）",
                          "先调用 canvas.get_node 查看节点媒体。")
    picked = _node_image_for_edit(node, node_id)
    source = await _load_node_image(project_id, node_id, picked)
    edited = await asyncio.to_thread(flip_image, source, direction)
    filename = f"edit-flip-{int(time.time() * 1000)}.png"
    result = await _replace_node_image(project_id, node, node_id, picked, edited, filename)
    result["direction"] = direction
    return result


async def outpaint_node_image_core(project_id: str, node_id, outpaint_size, offset) -> dict:
    """#289 canvas.edit_outpaint：服务端复刻站内红底扩图链（applyImageOutpaint 语义，ADR 0022 双面对齐）。

    站内手工扩图 = 扩大画布 + canvas 填高饱和红 #ff0044 + 原图按 (offsetX, offsetY) 贴回
    （static/js/canvas-image-editor.js:1944-1987）；本 core 用 PIL 同语义复刻：
    #ff0044 新画布 + 原图 source-over 贴放（半透明像素与红底混合，与 canvas drawImage 一致），
    结果作为节点图内容覆写（原资源保留可恢复），写 node.outpaintSize（含前端同语义的约分比例，
    canvasCore.reducedAspectRatio，smart-canvas.js:898）。红底是下游 generate.trigger 外区重绘的
    keying 底色——**绝不在服务端/模型侧发送 mask**（ADR 0007）。

    写操作：MCP 面施加 #242 确认门（CONFIRMATION_GATED_TOOLS）；错误文案带下一步建议（票面规范）。
    """
    from local_edit import OUTPAINT_FILL, outpaint_rgba, reduced_aspect_ratio

    node_id = str(node_id or "")
    node = _find_node_for_view(project_id, node_id)
    picked = _node_image_for_edit(node, node_id)
    try:
        target_width = int(outpaint_size.get("width"))
        target_height = int(outpaint_size.get("height"))
        off_x = int((offset or {}).get("x") or 0)
        off_y = int((offset or {}).get("y") or 0)
    except (TypeError, ValueError, AttributeError):
        raise agent_error(400, "INVALID_ARGUMENT",
                          "outpaint_size 必须是 {width, height} 正整数（offset 为 {x, y} 非负整数）",
                          "先调用 canvas.get_node 查看节点媒体尺寸，再按目标扩图范围传 outpaint_size。") from None
    if target_width <= 0 or target_height <= 0 or off_x < 0 or off_y < 0:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"outpaint_size 宽/高必须为正（本次 {target_width}x{target_height}），offset 必须为非负（本次 {off_x},{off_y}）",
                          "先调用 canvas.get_node 查看节点媒体尺寸后重试。")
    source = await _load_node_image(project_id, node_id, picked)
    try:
        outpainted = await asyncio.to_thread(outpaint_rgba, source, target_width, target_height, (off_x, off_y))
    except ValueError as exc:
        detail = str(exc)
        if "offset" in detail:
            raise agent_error(400, "INVALID_ARGUMENT",
                              f"offset ({off_x},{off_y}) 越界（原图 {source.width}x{source.height}，目标画布 {target_width}x{target_height}）",
                              "offset 须满足 0≤x、0≤y 且 x+原图宽≤目标宽、y+原图高≤目标高，先调用 canvas.get_node 查看尺寸后重试。") from None
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"目标画布必须完整含住原图（原图 {source.width}x{source.height}，目标 {target_width}x{target_height}）",
                          "先调用 canvas.get_node 查看节点媒体尺寸，再放大 outpaint_size 后重试。") from None
    outpaint_size = {
        "width": target_width,
        "height": target_height,
        "aspectRatio": reduced_aspect_ratio(target_width, target_height),
    }
    filename = f"edit-outpaint-{int(time.time() * 1000)}.png"
    result = await _replace_node_image(project_id, node, node_id, picked, outpainted, filename,
                                       extra_node_fields={"outpaintSize": outpaint_size})
    result["outpaint_size"] = outpaint_size
    result["fill"] = "#%02x%02x%02x" % OUTPAINT_FILL[:3]
    result["next_step"] = ("外区现为高饱和红（#ff0044，与站内红底扩图一致）：调用 generate.trigger 对节点重绘填充外区"
                          "（提示词含红底扩图语义，如 keep the existing image, extend the scene beyond the red border）；"
                          "画布尺寸信息见本结果 outpaint_size / node.outpaintSize")
    return result


async def erase_node_image_core(project_id: str, node_id, rect, prompt) -> dict:
    """#290 canvas.edit_erase core：节点图选区裁剪 → 生产 local-edit adapter → 合成回节点图。

    与站内局部编辑同一模型链（create_project_local_edit 的 modify 分支，
    app/routers/api_project_library.py:3546）：validate_rect → crop_rect → 裁剪区 PNG 作为
    request['image'] 交给 _app_main.local_edit_adapter（#290 起默认接
    _bailian_local_edit_adapter，qwen-image-edit-max-2026-01-16）→ 模型只收到裁剪图 + prompt，
    绝不发送 mask（ADR 0007）→ composite_rect 把模型结果贴回原图像素坐标 → 覆写节点图。

    写操作：MCP 面施加 #242 确认门（CONFIRMATION_GATED_TOOLS）；错误文案带下一步建议（票面规范）。
    """
    import inspect

    from local_edit import as_rgba, composite_rect, crop_rect, encode_png, validate_rect

    prompt = str(prompt or "").strip()
    if not prompt:
        raise agent_error(400, "INVALID_ARGUMENT",
                          "prompt 必填（描述要擦除什么、如何填补，如 erase the red cup）",
                          "先调用 canvas.view 查看节点图像内容，再补充 prompt 重试。整节点删除请用 canvas.delete_node。")
    node_id = str(node_id or "")
    node = _find_node_for_view(project_id, node_id)
    if not isinstance(rect, dict):
        raise agent_error(400, "INVALID_ARGUMENT",
                          "rect 必须是 {x, y, width, height}（原图像素坐标）",
                          "先调用 canvas.get_node 查看节点媒体。")
    try:
        x = float(rect.get("x") or 0)
        y = float(rect.get("y") or 0)
        w = float(rect.get("width") or 0)
        h = float(rect.get("height") or 0)
    except (TypeError, ValueError):
        raise agent_error(400, "INVALID_ARGUMENT", "rect 参数必须是数字",
                          "请检查 x/y/width/height 后重试。") from None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"rect 宽/高必须为正、原点必须为非负（本次 ({x},{y}) {w}x{h}）",
                          "先调用 canvas.get_node 查看节点媒体尺寸后重试。")
    picked = _node_image_for_edit(node, node_id)
    source = await _load_node_image(project_id, node_id, picked)
    try:
        crop_box = validate_rect([x, y, x + w, y + h], source.size)
    except ValueError:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"选区越界（图像 {source.width}x{source.height}，选区 ({x},{y}) {w}x{h}）",
                          "先调用 canvas.get_node 查看节点媒体尺寸，再检查 rect 后重试。") from None
    model_input = await asyncio.to_thread(encode_png, crop_rect(source, crop_box))
    adapter = _app_main.local_edit_adapter
    if adapter is None:
        raise agent_error(500, "SERVER_NOT_READY", "local_edit_adapter 未初始化",
                          "请检查服务启动配置后重试。")
    request_data = {
        'project_id': project_id,
        'mode': 'modify',
        'image': model_input,
        'references': [],
        'prompt': prompt,
        'selection': list(crop_box),
        'aspect_ratio': None,
        'size': f'{int(crop_box[2] - crop_box[0])}x{int(crop_box[3] - crop_box[1])}',
        'resolution': None,
        'source_size': [source.width, source.height],
    }
    store = _app_main.FIREFLY_PROJECT_STORE
    context = _media_context(project_id)
    guarded = set()
    lifecycle = None
    try:
        if context:
            _media_root(store, project_id, context,
                        "external erase provider call attempted; charges or other irreversible effects may occur", guarded)
            lifecycle = await _media_start(project_id, context, [node_id])
        try:
            if inspect.iscoroutinefunction(adapter):
                adapter_result = await adapter(request_data)
            else:
                from app.services.agent_ops_async import run_blocking_to_completion
                adapter_result = await run_blocking_to_completion(adapter, request_data)
                if inspect.isawaitable(adapter_result):
                    adapter_result = await adapter_result
        except (OSError, RuntimeError, ModelRoutingError) as exc:
            raise agent_error(502, "UPSTREAM_MODEL_ERROR", f"局部编辑模型调用失败：{exc}",
                              "请检查 bailian 凭据/网络后重试（原图未改动）。") from exc
        model_image = await asyncio.to_thread(as_rgba, adapter_result)
        result_image = await asyncio.to_thread(composite_rect, source, model_image, crop_box, 0)
        filename = f"edit-erase-{int(time.time() * 1000)}.png"
        result = await _replace_node_image(project_id, node, node_id, picked, result_image, filename)
        result["rect"] = [x, y, w, h]
        result["selection"] = list(crop_box)
        result["next_step"] = ("选区已按 prompt 重绘并贴回节点图（原资源保留在资源库，可恢复）；"
                               "调用 canvas.view 查看擦除结果，不满意可调整 prompt 或重新选区再调用 canvas.edit_erase。")
        if context:
            result["op_id"] = context.op_id
        return result
    except (Exception, asyncio.CancelledError) as exc:
        if guarded:
            if isinstance(exc, HTTPException) and exc.status_code == 409:
                raise agent_error(409, "CONFLICT", "Node image changed during erase; the external provider call cannot be undone",
                                  "画布未被本次擦除覆写；检查操作日志及媒体资源后再决定是否重试。",
                                  retryable=False, details={"op_id": context.op_id, "status": "conflict",
                                  "irreversible_effects": ["external erase provider call attempted; charges or other irreversible effects may occur"]}) from exc
            raise _media_partial(context, exc) from exc
        raise
    finally:
        try:
            if lifecycle:
                await _media_end(project_id, *lifecycle)
        finally:
            if guarded:
                _generation_release(store, project_id, context.op_id, guarded)


@router.post("/edit_erase")
async def canvas_edit_erase(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#290 canvas.edit_erase: 按 prompt 擦除节点图选区内容。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_erase", body,
                               f"擦除节点 {body.get('node_id')} 选区图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_erase", body,
                                  lambda: erase_node_image_core(project_id, body.get("node_id"), body.get("rect"), body.get("prompt")), agent_node_id)


# ── #357：服务端确认门（ADR 0022 实化门从客户端/网关移到统一执行入口）──────────
# 判定：带 X-Agent-Project 的请求 = agent 写操作 → 先过门（复用 request_confirmation_core，
# 含 autonomy=autonomous 自动放行与 SAFETY_FLOOR_TOOLS 兑底）；浏览器用户路径不进本门
# （前端 #347 ffDialog 已在发起侧确认）。MCP 网关（api_mcp._confirm_gate）同用本 core。
# 超时 >60s（review 口径：pi 写操作的等待必须大于旧 60s 门槛）。
AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS = 90.0


class AgentConfirmationDenied(Exception):
    """确认门拒绝/超时（REST 层转 409；MCP 层转 McpToolError）。"""


async def _server_confirm_gate(request: Request, project_id: str, tool: str,
                               args: dict, message: str) -> None:
    """统一执行入口上的确认门：仅 agent 请求（X-Agent-Project）生效。"""
    if not request.headers.get("X-Agent-Project", ""):
        return
    agent_node_id = request.query_params.get("agent_node_id", "")
    if not agent_node_id:
        raise agent_error(400, "CONFIRM_MISSING_NODE_ID",
                          "agent 写操作必须携带 ?agent_node_id=<发起节点>（确认门需要归属节点以投影到其会话）",
                          "重新发起请求并在查询串里带 agent_node_id=<你的节点 ID>。")
    decision = await request_confirmation_core(
        project_id, agent_node_id, tool, args or {}, message,
        AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS)
    if decision.get("approved"):
        return
    if decision.get("reason") == "rejected":
        raise agent_error(409, "CONFIRM_REJECTED", f"用户在画布上拒绝了操作：{tool}",
                          "用户拒绝了这个操作；不要重试同一参数，先问用户想要什么。", retryable=False)
    raise agent_error(409, "CONFIRM_PENDING",
                      f"确认超时（{int(AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS)} 秒）：{tool} 未获确认",
                      "画布标签页可能已关闭，或确认仍在等待——先调用 canvas.view 查看画布状态，再决定是否重试。")
