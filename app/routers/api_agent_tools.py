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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
import json
import sqlite3
import uuid
import weakref
from typing import Callable

from app.services.agent_node_state import ownership_fields, project_nodes
from app.services.agent_confirmation import generation_settlement, originating_turn, resumable, validate_pending

import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK, ensure_agent_same_origin
from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP
from app.services import agent_batch_prompt, agent_frame, agent_image_settings, agent_layout, agent_refs
from app.services.agent_legacy_image_refs import LegacyImage, archive_legacy_images, inspect_legacy_images, unavailable
from firefly_withdrawn_refs import AUTO_ARCHIVED_REFS
from app.services.agent_errors import agent_error
from app.services.agent_tools_manifest import LAYOUT_TOOLS, UPDATABLE_NODE_FIELDS as _UPDATABLE_NODE_FIELDS
from app.core import ONLINE_IMAGE_REFERENCE_MAX, PROJECT_IMAGE_GENERATION_FUTURES, ProjectImageGenerationRequest
from firefly_generation import ImageGenerationRequest, ModelRoutingError
from firefly_projects import ProjectNotFound, ProjectStoreError

# #240：agent-tools 系列端点接入同源/Host 守卫（与现有 API 一致；
# 无 Origin 的本机回环 sidecar 调用按票面模型放行）。
router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"], dependencies=[Depends(ensure_agent_same_origin)])

generate_router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["agent-generate"], dependencies=[Depends(ensure_agent_same_origin)])

media_router = APIRouter(prefix="/api/projects/{project_id}/media", tags=["agent-media"], dependencies=[Depends(ensure_agent_same_origin)])

viewport_router = APIRouter(prefix="/api/projects/{project_id}/viewport", tags=["agent-viewport"], dependencies=[Depends(ensure_agent_same_origin)])

confirm_router = APIRouter(prefix="/api/projects/{project_id}/agent", tags=["agent-confirm"], dependencies=[Depends(ensure_agent_same_origin)])

def require_agent_project(request: Request) -> None:
    """Only the dedicated tool writes require an explicit Agent context; this is not authentication."""
    if not request.headers.get("X-Agent-Project", "").strip():
        raise agent_error(400, "INVALID_ARGUMENT", "X-Agent-Project is required for Agent tool writes",
                          "请在请求中携带 X-Agent-Project 项目 ID。")


_AGENT_WRITE = [Depends(require_agent_project)]


@confirm_router.post('/present', dependencies=_AGENT_WRITE)
async def present_agent(project_id: str, request: Request):
    from app.services import agent_present
    from app.services.agent_host import agent_host_manager
    if request.headers.get('X-Agent-Project') != project_id:
        raise agent_error(403, 'PROJECT_MISMATCH', 'X-Agent-Project must match the requested project')
    _validate_project(project_id)
    try:
        args = await request.json()
    except ValueError as exc:
        raise agent_refs.invalid('Expected a JSON object') from exc
    return await agent_present.agent_present_call(_app_main.FIREFLY_PROJECT_STORE, agent_host_manager, project_id,
                                    request.query_params.get('agent_node_id', ''), args, binding='rest')


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
                              "canvas.connect", "canvas.disconnect", "canvas.place_media",
                              "canvas.attach_refs", "canvas.set_ref_order", "canvas.batch_prompt_edit", "plan.apply", *LAYOUT_TOOLS})


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
    from app.services.agent_batch import shutdown
    shutdown(loop)
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
        validate_pending(project_id, before, admit=True)
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
    require_agent_project(request)
    agent_node_id = agent_node_id or request.query_params.get("agent_node_id", "")
    if not agent_node_id:
        raise agent_error(400, "INVALID_ARGUMENT", "Agent node identity is required for operation journaling",
                          "请在查询串传入发起操作的 agent_node_id，再重试。")
    from app.services.agent_host import agent_host_manager
    turn = originating_turn() or agent_host_manager.get_current_turn_id(project_id, agent_node_id)
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
    validate_pending(project_id, store.canvas(project_id), admit=True)
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
    trusted_agent_bindings: bool = False, deletion=None, recheck_before_cas=None,
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
                    validate_pending(project_id, canvas)
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
                        if recheck_before_cas is not None:
                            with store._lock:
                                recheck_before_cas(store.canvas(project_id))
                                return _apply_journaled(store, project_id, mutations, revision, trusted_agent_bindings,
                                                        context, deletion, persisted)
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


@router.post("/nodes", dependencies=_AGENT_WRITE)
async def create_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a new node on the canvas. 可选 `agent_node_id`（#356 D11）：传了则
    未显式给 x/y 时自动落位；Agent 缺少会话框时自动建立（与 MCP 同行为）。"""
    body = await request.json()
    agent = _creation_context(request, body.get("agent_node_id", ""))
    return await _rest_agent_call(request, project_id, "canvas.create_node", body,
                                  lambda: create_node_core(project_id, body.get("node", {}), agent_node_id=agent), agent)


async def _rest_reference_tool(project_id, request, tool, core):
    args = await request.json()
    if not isinstance(args, dict):
        raise agent_refs.invalid("Tool arguments must be an object")
    agent = _creation_context(request, args.pop("agent_node_id", ""))
    return await _rest_agent_call(request, project_id, tool, args,
                                  lambda: core(project_id, args, agent_node_id=agent), agent)


@router.post("/place_media", dependencies=_AGENT_WRITE)
async def place_media(project_id: str, request: Request):
    return await _rest_reference_tool(project_id, request, "canvas.place_media", place_media_core)


@router.post("/attach_refs", dependencies=_AGENT_WRITE)
async def attach_refs(project_id: str, request: Request):
    return await _rest_reference_tool(project_id, request, "canvas.attach_refs", attach_refs_core)


@router.post("/set_ref_order", dependencies=_AGENT_WRITE)
async def set_ref_order(project_id: str, request: Request):
    return await _rest_reference_tool(project_id, request, "canvas.set_ref_order", set_ref_order_core)


def _layout_route(tool):
    async def endpoint(project_id: str, request: Request):
        try:
            args = await request.json()
        except ValueError as exc:
            raise agent_layout.invalid("Expected a JSON object") from exc
        agent_layout.validate_args(tool, args)
        owner = request.query_params.get("agent_node_id", "")
        return await _rest_agent_call(request, project_id, tool, args,
                                      lambda: layout_tool_core(project_id, owner, tool, args,
                                                               hosted=True), owner)
    return resumable(endpoint)


for _layout_tool in sorted(LAYOUT_TOOLS):
    _layout_path = _layout_tool.split(".", 1)[1]
    router.add_api_route(f"/{_layout_path}", _layout_route(_layout_tool), methods=["POST"], name=_layout_path,
                         dependencies=_AGENT_WRITE)


@router.patch("/nodes/{node_id}", dependencies=_AGENT_WRITE)
async def update_node(project_id: str, node_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Update an existing node's properties."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "canvas.update_node", {"node_id": node_id, "updates": body},
                                  lambda: update_node_core(project_id, node_id, body))


@router.delete("/nodes/{node_id}", dependencies=_AGENT_WRITE)
@resumable
async def delete_node(project_id: str, node_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Delete a node after confirmation; shared delete core also tears down any Agent host."""
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


@router.post("/edges", dependencies=_AGENT_WRITE)
async def create_edge(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a connection between two nodes."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "canvas.connect", body,
                                  lambda: create_edge_core(project_id, body.get("edge", {})))


@router.delete("/edges/{from_node}/{to_node}", dependencies=_AGENT_WRITE)
@resumable
async def delete_edge(project_id: str, from_node: str, to_node: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Disconnect only after the server has verified ownership or approval."""
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


@router.post("/edit_crop", dependencies=_AGENT_WRITE)
@resumable
async def canvas_edit_crop(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Crop a node image after confirmation, retaining the original in the media library."""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_crop", body,
                               f"裁剪节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_crop", body,
                                  lambda: crop_node_image_core(project_id, body.get("node_id"), body.get("rect")), agent_node_id)


@router.post("/batch_prompt_edit")
@resumable
async def canvas_batch_prompt_edit(project_id: str, request: Request):
    args = await request.json()
    parsed = agent_batch_prompt.parse(args)
    if not isinstance(parsed, str):
        return await batch_prompt_edit_core(project_id, request.query_params.get("agent_node_id", ""), parsed)
    require_agent_project(request)  # Preview on this URL is read-only.
    actor = _paid_rest_actor(request)
    return await _rest_agent_call(request, project_id, "canvas.batch_prompt_edit", args,
                                  lambda: batch_prompt_edit_core(project_id, actor, parsed), actor)


@router.post("/batch_edit", dependencies=_AGENT_WRITE)
@resumable
async def canvas_batch_edit(project_id: str, request: Request):
    body = agent_refs.require_object(await request.json(), "batch_edit", required=("node_ids", "prompt"))
    node_ids = body["node_ids"]
    agent = _paid_rest_actor(request)
    prompt, plans = await prepare_batch_generation(project_id, node_ids, body["prompt"])
    count = sum(plan.request.count for plan in plans)
    edited, human = legacy_batch_prompt_counts(plans, prompt)
    await confirm_batch_prompt(project_id, agent, "canvas.batch_edit", edited, human, images=count,
                               preflight=[generation_confirmation_input(project_id, node, plan)
                                          for node, plan in zip(node_ids, plans)])
    enforce_generation_limit(project_id, agent, count=count)
    return await _rest_agent_call(request, project_id, "canvas.batch_edit", body,
                                  lambda: batch_edit_core(project_id, node_ids, prompt,
                                                          agent_node_id=agent, plans=plans), agent)


@router.post("/edit_rotate", dependencies=_AGENT_WRITE)
@resumable
async def canvas_edit_rotate(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#266b canvas.edit_rotate: 本地旋转节点图（90/180/270 逆时针）覆写节点图。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_rotate", body,
                               f"旋转节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_rotate", body,
                                  lambda: rotate_node_image_core(project_id, body.get("node_id"), body.get("angle")), agent_node_id)


@router.post("/edit_flip", dependencies=_AGENT_WRITE)
@resumable
async def canvas_edit_flip(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#266b canvas.edit_flip: 本地镜像节点图（horizontal 左右 / vertical 上下）覆写节点图。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_flip", body,
                               f"镜像节点 {body.get('node_id')} 的图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_flip", body,
                                  lambda: flip_node_image_core(project_id, body.get("node_id"), body.get("direction")), agent_node_id)


@router.post("/edit_outpaint", dependencies=_AGENT_WRITE)
@resumable
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

    if "layoutFrame" in node:
        raise agent_layout.invalid("layoutFrame is assigned only by canvas.group_create")
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
        created, mutations = _build_placement(_placement_nodes(canvas), node, agent_node_id,
                                             frame_id=frame_id, explicit_position=explicit_position)
        return mutations

    created = None
    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result({"success": True, "node_id": node["id"], **_geometry(created)})


def _build_placement(nodes, node, agent_node_id, *, frame_id=None, explicit_position=False, group=None):
    agent = nodes.get(agent_node_id, {})
    bindings = []
    frame = group
    if frame is None and not explicit_position and agent.get("type") in (NODE_TYPE_AGENT, "agent"):
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
    return created, [{"op": "upsert_node", "node": created}, *bindings]


def _placement_nodes(canvas):
    return {node["id"]: node for node in canvas.get("nodes", [])}


def _geometry(node):
    return {key: node[key] for key in ("x", "y", "w", "h")}


def _media_card(image, agent_node_id, *, title=None):
    return {"id": f"node-{uuid.uuid4().hex[:12]}", "type": "smart-image",
            "title": image["name"] if title is None else title, "images": [image],
            "created_at": _app_main.now_ms(), **ownership_fields(agent_node_id)}


async def layout_tool_core(project_id: str, owner: str, tool: str, args: dict, *, hosted=False) -> dict:
    from app.services.agent_host import agent_host_manager
    from app.services.agent_ops_context import agent_ops_scope, current_agent_ops_context

    agent_layout.validate_args(tool, args)
    if not isinstance(owner, str) or not owner.strip():
        raise agent_layout.invalid("Layout tools require the initiating Agent node ID")
    _validate_project(project_id)
    context = current_agent_ops_context()
    if context is not None and (context.project_id, context.agent_node_id, context.tool) != (project_id, owner, tool):
        raise agent_layout.invalid("Layout operation context does not match its project, agent and tool")

    async def execute():
        store = _app_main.FIREFLY_PROJECT_STORE
        group_id = f"node-{uuid.uuid4().hex[:12]}" if tool == "canvas.group_create" else None
        now = _app_main.now_ms()

        def plan(canvas):
            return agent_layout.make_plan(canvas, tool, args, owner, group_id, now)

        snapshot = store.canvas(project_id)
        preview = plan(snapshot)
        active_turn = current_agent_ops_context().turn_id if hosted else None
        if preview.confirm:
            existing = {node['id'] for node in snapshot.get('nodes', [])}
            details = {"nodes": preview.confirm, "group_id": preview.result.get("group_id")}
            targets = [mutation['node']['id'] for mutation in preview.mutations
                       if mutation['node']['id'] in existing]
            names = "、".join(f"{entry['title']} ({entry['id']})" for entry in preview.confirm[:8])
            more = f" 等 {len(preview.confirm)} 个节点" if len(preview.confirm) > 8 else ""
            action = "加入新排版框并移动" if tool == "canvas.group_create" else "移动"
            named_nodes = agent_host_manager.layout_named_nodes(project_id, owner, active_turn)
            decision = await request_confirmation_core(project_id, owner, tool, details,
                f"是否{action}以下受保护节点：{names}{more}？", AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS,
                allow_automatic={entry['id'] for entry in preview.confirm}.issubset(named_nodes),
                prepared_target_node_ids=targets)
            if decision.get("approved") is not True:
                if decision.get("reason") == "rejected":
                    raise agent_error(409, "CONFIRM_REJECTED", "User rejected the layout operation",
                                      "不要重试相同操作；先询问用户。", retryable=False)
                raise agent_error(409, "CONFIRM_PENDING", "Layout confirmation was not received",
                                  "确认画布标签页已打开，再查看最新画布状态。")

        def build(canvas):
            nonlocal preview
            if preview.confirm:
                # Approval covers exactly this snapshot, not a replan that
                # could introduce newly protected cards or different positions.
                if (canvas.get("revision") != snapshot.get("revision") or
                        (active_turn and agent_host_manager.get_current_turn_id(project_id, owner) != active_turn)):
                    raise agent_error(409, "CANVAS_CONFLICT", "Canvas or active turn changed while layout confirmation was pending",
                                      "重新查看画布并发起新的确认，不复用旧批准。")
            else:
                preview = plan(canvas)
                if preview.confirm:
                    raise agent_error(409, "CANVAS_CONFLICT", "Layout now requires user confirmation",
                                      "重新查看画布并发起新的确认。")
            return preview.mutations

        await _apply_mutations_from_snapshot(project_id, build)
        return _op_result(preview.result)

    if context is None:
        with agent_ops_scope(project_id=project_id, agent_node_id=owner, tool=tool, args=args):
            return await execute()
    return await execute()


async def place_media_core(project_id: str, args: dict, agent_node_id: str = "") -> dict:
    _validate_project(project_id)
    agent_refs.require_object(args, "place_media", required=("asset_id",), optional=("title", "node_id", "group_id"))
    asset_id = agent_refs.validate_asset_id(args["asset_id"])
    for key in ("node_id", "group_id"):
        if key in args:
            agent_refs.identifier(args[key], key)
    title = args.get("title")
    if "title" in args and not isinstance(title, str):
        raise agent_refs.invalid("title must be a string")
    target, group_id = args.get("node_id"), args.get("group_id")
    result = {}

    def build(canvas):
        nodes = _placement_nodes(canvas)
        image = agent_refs.media_image(_app_main.FIREFLY_PROJECT_STORE, project_id, asset_id, reference=False)
        group = nodes.get(group_id)
        if group_id and (not group or group.get("type") != NODE_TYPE_GROUP):
            raise agent_error(404, "NODE_NOT_FOUND", "Group not found", "选择 canvas.list_nodes 返回的组框。",
                              details={"group_id": group_id})
        if not target:
            card = _media_card(image, agent_node_id, title=title)
            placed, mutations = _build_placement(nodes, card, agent_node_id, group=group,
                                                  frame_id=f"node-{uuid.uuid4().hex[:12]}")
        else:
            node = _snapshot_node(canvas, target)
            if node.get("type") not in agent_refs.IMAGE_TYPES:
                raise agent_refs.invalid("place_media needs an image node")
            if group and target not in (group.get("items") or []):
                raise agent_refs.invalid("The existing image node is not a member of the requested group")
            images = list(node.get("images") or [])
            placed = dict(node)
            if not any(isinstance(item, dict) and item.get("asset_id") == asset_id for item in images):
                placed["images"] = [*images, image]
            if title is not None:
                placed["title"] = title
            mutations = [] if node == placed else [{"op": "upsert_node", "node": placed}]
        w, h = agent_frame.node_size(placed)
        result.update(node_id=placed["id"], x=agent_frame.coordinate(placed.get("x")),
                      y=agent_frame.coordinate(placed.get("y")), w=w, h=h)
        return mutations

    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result(result)


async def attach_refs_core(project_id: str, args: dict, agent_node_id: str = "") -> dict:
    mode, refs = agent_refs.parse_attachments(args)

    def edit(canvas, node, before):
        target = node["id"]
        nodes = _placement_nodes(canvas)
        additions = []

        def resolve(source, existing):
            if "node_id" in source:
                source_id = source["node_id"]
                images = nodes.get(source_id, {}).get("images") or []
                index = source.get("image_index", 0)
                selected = images[index] if isinstance(images, list) and index < len(images) else None
                asset = selected.get("asset_id") if isinstance(selected, dict) else None
                if not asset or source_id == target:
                    raise agent_error(400, "REF_NOT_IN_PROJECT", "Source does not name a usable project image",
                                      "先用 media.upload / canvas.place_media，或选择另一个来源节点。")
            else:
                asset = source["asset_id"]
                source_id = (existing.get(asset) or {}).get("from")
                if source_id == target or source_id not in nodes:
                    source_id = next((key for key, candidate in nodes.items() if key != target
                                      and any(isinstance(image, dict) and image.get("asset_id") == asset
                                              for image in candidate.get("images") or [])), None)
            image = agent_refs.media_image(_app_main.FIREFLY_PROJECT_STORE, project_id,
                                           agent_refs.validate_asset_id(asset), reference=True)
            if not source_id:
                card = _media_card(image, agent_node_id)
                placed, mutations = _build_placement(nodes, card, agent_node_id,
                                                      frame_id=f"node-{uuid.uuid4().hex[:12]}")
                for mutation in mutations:
                    changed = mutation["node"]
                    nodes[changed["id"]] = changed
                additions.extend(mutations)
                source_id = placed["id"]
            return asset, source_id

        return agent_refs.attach(before, refs, mode, target, resolve), additions

    return await _edit_agent_references(project_id, args["node_id"], edit)


async def set_ref_order_core(project_id: str, args: dict, agent_node_id: str = "") -> dict:
    order, overrides = agent_refs.parse_reorder(args)
    return await _edit_agent_references(project_id, args["node_id"],
                                        lambda _, node, before: (agent_refs.reorder(before, order, overrides), []))


async def _edit_agent_references(project_id, target, edit):
    _validate_project(project_id)
    target = agent_refs.identifier(target, "node_id")
    result = {}

    def build(canvas):
        node = _snapshot_node(canvas, target)
        if node.get("type") not in agent_refs.IMAGE_TYPES:
            raise agent_error(400, "NODE_NOT_GENERATABLE", "Target is not an image generation node",
                              "选择可生成图片的节点；先调用 canvas.list_nodes。")
        before = agent_refs.managed_edges(canvas, target)
        after, mutations = edit(canvas, node, before)
        result.update(agent_refs.ref_result(after))
        if before != after:
            mutations.extend(agent_refs.edge_changes(before, after))
            # Do not rewrite manual inputs, excludes or original prompt.
            ids = [edge["asset_id"] for edge in after if edge["enabled"]]
            if node.get("reference_asset_ids") != ids:
                mutations.append({"op": "upsert_node", "node": {**node, "reference_asset_ids": ids}})
        return mutations

    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result(result)


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
    run_patch = agent_image_settings.validate_patch(updates["runSettings"]) if "runSettings" in updates else None
    _validate_project(project_id)
    updated_at = _app_main.now_ms()
    result = {"success": True, "node_id": node_id}

    def build(canvas):
        target_node = _snapshot_node(canvas, node_id)
        # Session-frame membership is maintained only by the placement chain.
        if "items" in updates and target_node.get("type") == "smart-group" and target_node.get("chatFrame") is True:
            raise agent_error(400, "INVALID_ARGUMENT",
                              "会话框 items 由服务端落位链维护，canvas.update_node 不可覆写",
                              "会话框成员由服务端维护；不要改 items，用 canvas.create_node 在会话框内建节点。")
        from app.services.agent_ops_context import current_agent_ops_context
        from app.services.agent_ops_delta import _equal
        fields = {key: value for key, value in updates.items() if key != "runSettings"}
        prev = {key: target_node.get(key) for key in fields}
        if run_patch is not None:
            stored = target_node.get("runSettings")
            if stored is not None and not isinstance(stored, dict):
                raise agent_image_settings.invalid("Saved runSettings is not an object")
            merged, prev["runSettings"] = agent_image_settings.merge_patch(stored or {}, run_patch)
            if not _equal(stored or {}, merged):
                context = current_agent_ops_context()
                owner = target_node.get("agentNodeId")
                if (target_node.get("createdBy") != "agent" or not owner or
                        target_node.get("userTouchedAt") is not None or
                        (context and context.agent_node_id != owner)):
                    raise agent_error(403, "NOT_ALLOWED_ON_USER_NODE", "Agent cannot overwrite user generation settings",
                                      "请用户在节点参数面板修改设置，或改用未被人工编辑的自有节点。")
                fields["runSettings"] = merged
        result["prev"] = prev
        if all(key in target_node and _equal(target_node[key], value) for key, value in fields.items()):
            return []
        return [{"op": "upsert_node", "node": {**target_node, **fields, "updated_at": updated_at}}]

    await _apply_mutations_from_snapshot(project_id, build)
    return _op_result(result)


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

# #351: session quota is in requested images, not task count.
# 内存计数，键 (project_id, agent_node_id)；节点删除/会话 drop 时清理。
GENERATION_SESSION_LIMIT = int(os.environ.get("FIREFLY_AGENT_GENERATION_LIMIT", "200"))
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
    from app.services import agent_batch
    agent_batch.invalidate(project_id, agent_node_id)
    with CANVAS_TASK_LOCK:
        for key in [k for k in _GENERATION_COUNTS
                    if k[0] == project_id and (not agent_node_id or k[1] == agent_node_id)]:
            _GENERATION_COUNTS.pop(key, None)


@generate_router.post("/trigger", dependencies=_AGENT_WRITE)
@resumable
async def trigger_generation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    body = agent_refs.require_object(await request.json(), "generate.trigger", required=("node_id",))
    agent = _paid_rest_actor(request)
    node_id = body["node_id"]
    plan = await prepare_generation(project_id, node_id)
    count = plan.request.count
    await _server_confirm_gate(request, project_id, "generate.trigger",
                               {"node_id": node_id, "n": count, "preflight": generation_confirmation_input(project_id, node_id, plan)},
                               f"节点 {node_id} 生成 {count} 张图片（消耗外部生成额度）")
    enforce_generation_limit(project_id, agent, count=count)
    return await _rest_agent_call(request, project_id, "generate.trigger", body,
                                  lambda: trigger_generation_core(project_id, node_id, agent_node_id=agent, plan=plan), agent)


@generate_router.post('/retry', dependencies=_AGENT_WRITE)
@resumable
async def retry_generation(project_id: str, request: Request):
    from app.services import agent_batch, agent_generation_retry

    actor = _paid_rest_actor(request)
    if request.headers['X-Agent-Project'] != project_id:
        raise agent_error(403, 'PROJECT_MISMATCH', 'Agent context does not match the project')
    try:
        args = agent_generation_retry.arguments(await request.json())
    except ValueError as exc:
        raise agent_refs.invalid('Expected a JSON object') from exc
    caller = agent_batch.Caller(project_id, actor)
    return await _rest_agent_call(request, project_id, 'generate.retry', args,
                                  lambda: agent_generation_retry.execute(caller, args), actor)


@generate_router.post('/preflight', dependencies=_AGENT_WRITE)
@generate_router.post('/request_batch', dependencies=_AGENT_WRITE)
@generate_router.post('/batch_status', dependencies=_AGENT_WRITE)
@generate_router.post('/stop_batch', dependencies=_AGENT_WRITE)
async def generation_batch_tool(project_id: str, request: Request):
    from app.services import agent_batch

    actor = _paid_rest_actor(request)
    if request.headers['X-Agent-Project'] != project_id:
        raise agent_error(403, 'PROJECT_MISMATCH', 'Agent context does not match the project')
    try:
        args = await request.json()
    except ValueError as exc:
        raise agent_refs.invalid('Expected a JSON object') from exc
    tool = 'generate.' + request.url.path.rsplit('/', 1)[1]
    return await agent_batch.call(tool, agent_batch.Caller(project_id, actor), args)


def _paid_rest_actor(request):
    require_agent_project(request)
    actor = request.query_params.get("agent_node_id", "")
    if not actor:
        raise agent_error(400, "CONFIRM_MISSING_NODE_ID", "agent_node_id is required for confirmation")
    return _creation_context(request, actor)


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
        validate_pending(project_id, store.canvas(project_id), admit=True)
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


@dataclass(frozen=True)
class _PreparedGeneration:
    request: ImageGenerationRequest
    payload: ProjectImageGenerationRequest
    route: object
    node_type: str
    original_prompt: tuple
    legacy_images: tuple[LegacyImage, ...] = ()
    user_prompt: str = ''
    final_prompt: str = ''
    reference_roles: tuple = ()
    reference_files: tuple = ()
    provider: dict | None = None
    batch_input: tuple | None = None


def generation_confirmation_input(project_id, node_id, plan):
    legacy = {image.asset_id: image.url for image in plan.legacy_images}
    roles = plan.reference_roles or (None,) * len(plan.payload.reference_asset_ids)
    return {'node_id': node_id, 'final_prompt': plan.final_prompt, 'user_prompt': plan.user_prompt,
            'refs': [{'n': n, 'asset_id': asset, 'role': role or 'general',
                      'thumb': legacy.get(asset, f'/api/project-library/{project_id}/media/{asset}')}
                     for n, (asset, role) in enumerate(zip(plan.payload.reference_asset_ids, roles), 1)],
            'model': plan.route.model, 'provider': plan.route.provider_id,
            'aspect_ratio': plan.request.aspect_ratio, 'size': plan.payload.size, 'count': plan.request.count}


def _node_prompt_fields(node):
    return tuple(node.get(key) for key in ("prompt", "text", "promptEditedByUser",
                                            "promptDraftText", "promptDraftHtml", "created_at"))


async def prepare_generation(project_id, node_id, *, canvas=None, prompt_override=None):
    agent_refs.identifier(node_id, "node_id")
    store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        raise agent_error(500, 'SERVER_NOT_READY', 'Project store not initialized')
    try:
        saved, catalog = await asyncio.to_thread(store.image_reference_snapshot, project_id, include_canvas=canvas is None)
    except ProjectNotFound as exc:
        raise agent_error(404, 'PROJECT_NOT_FOUND', f'Project not found: {project_id}') from exc
    except ProjectStoreError as exc:
        raise agent_error(400, 'INVALID_ARGUMENT', str(exc), '请先在浏览器中打开画布。') from exc
    return await asyncio.to_thread(resolve_generation, project_id, node_id, saved if canvas is None else canvas,
                                   catalog, saved=saved, prompt_override=prompt_override)


def resolve_generation(project_id, node_id, canvas, catalog, *, saved=None, prompt_override=None,
                       providers=None, preflight=False, graph=None, selection=None):
    from app.routers.api_project_library import project_image_executed_prompt, project_image_request
    from app.services.canvas_image_refs import ReferenceGraph
    from app.services.canvas_image_selection import require_canvas_image_provider, route_canvas_image

    store = _app_main.FIREFLY_PROJECT_STORE
    node = _snapshot_node(canvas, node_id)
    graph = graph or ReferenceGraph(canvas, project_id, catalog)
    if selection is None:
        selection = graph.resolve(node_id, prompt_override=prompt_override, track_saved_mentions=saved is not None)
    positions = list(graph.image_positions(selection))
    prompt = selection['prompt']
    if preflight and not prompt:
        raise agent_error(400, 'NO_PROMPT', f'Node {node_id} has no prompt')
    legacy = [position for position, asset, _ in positions if not asset]
    if legacy and (saved is None or not set(legacy) <= selection['saved_mention_positions']):
        raise unavailable()
    if len(positions) > ONLINE_IMAGE_REFERENCE_MAX:
        raise agent_image_settings.invalid('Selected reference images exceed the application safety limit')
    files = ()
    if owned := [asset for _, asset, _ in positions if asset]:
        try:
            files = store.check_image_reference_files(project_id, owned, fingerprint=True)
        except ProjectStoreError as exc:
            raise agent_error(400, 'ASSET_NOT_FOUND', str(exc),
                              '参考图资产不可用；请先导入或归档，再重新挂图。') from exc
    selected_urls = {position: selection['refs'][position]['url'] for position in legacy}
    images = inspect_legacy_images(_app_main, store, saved, selected_urls.values()) if legacy else {}
    refs = [(asset or images[selected_urls[position]].asset_id, role) for position, asset, role in positions]
    if not prompt:
        raise agent_error(400, "NODE_NOT_GENERATABLE", f"Node {node_id} has no prompt",
                          "先给节点写 prompt，再触发生成。")
    # #391：无提示词属于节点不可生成；服务商准入只检查已有提示词的请求。
    try:
        require_canvas_image_provider(agent_image_settings.node_image_provider_id(node))
    except ModelRoutingError as exc:
        raise agent_error(400, 'INVALID_ARGUMENT', exc.message, details={'routing': exc.to_dict()}) from exc
    if providers is None:
        providers = _app_main.load_api_providers()
    try:
        wire = ProjectImageGenerationRequest(
            **agent_image_settings.image_settings(node, canvas, providers, _app_main.model_spec_for),
            prompt=prompt,
            reference_asset_ids=[asset for asset, _ in refs],
            reference_roles=[role for _, role in refs] if any(role for _, role in refs) else [],
            result_node_id=node_id, base_revision=canvas.get("revision", 0),
        )
        request = project_image_request(wire)
        route = route_canvas_image(request, wire, providers, _app_main.route_image_generation)
        final_prompt = project_image_executed_prompt(wire)
    except ModelRoutingError as exc:
        raise agent_error(400, 'NO_ROUTE' if preflight else 'INVALID_ARGUMENT', exc.to_dict()["message"],
                          "换一个可用的 provider/model 或调整参数后重试。",
                          details={"routing": exc.to_dict()}) from exc
    except ValueError as exc:
        raise agent_image_settings.invalid(str(exc)) from exc
    return _PreparedGeneration(request, wire, route, str(node.get("type", "")), _node_prompt_fields(node),
                               tuple(images.values()), selection['displayPrompt'], final_prompt,
                               tuple(role for _, role in refs), files,
                               next((p for p in providers if p.get('id') == route.provider_id), None))


async def trigger_generation_core(project_id: str, node_id: str, agent_node_id: str = "", *, plan=None,
                                  validate_start=None, approved_provider=None, retry_caller=None,
                                  retry_parent=None, retry_models=(), retry_batch_id=None) -> dict:
    if plan is None:
        plan = await prepare_generation(project_id, node_id)
    creation = ownership_fields(agent_node_id)
    prompt = plan.request.prompt

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

    from app.routers.api_project_library import create_project_image_task_core
    from app.services.agent_ops_context import current_agent_ops_context

    context = current_agent_ops_context()
    batch = _BATCH_GENERATION.get()
    if not (context and context.project_id == project_id and context.agent_node_id
            and (context.tool in {'generate.trigger', 'generate.retry'} or
                 (context.tool in {'canvas.batch_edit', 'generate.request_batch'} and batch is not None))):
        context = None
    hooks = _AgentImageLifecycle(project_id, task_id, node_id, result_node_id, context, build_result,
                                 legacy_images=plan.legacy_images, validate_start=validate_start,
                                 approved_provider=approved_provider, plan=plan, retry_caller=retry_caller,
                                 retry_parent=retry_parent, retry_models=retry_models, retry_batch_id=retry_batch_id)
    payload = plan.payload.model_copy(update={'result_node_id': result_node_id, 'idempotency_key': task_id})
    await create_project_image_task_core(project_id, payload, prepared=(plan.request, plan.route), hooks=hooks)
    if context:
        await _broadcast_canvas_updated(project_id, source='agent')
    response = {'task_id': task_id, 'type': 'image', 'node_id': node_id,
                'result_node_id': result_node_id, **_geometry(result_node)}
    if context:
        response['op_id'] = context.op_id
        response['touched_node_ids'] = _operation_touched(hooks.store, project_id, hooks.guarded | {context.op_id})
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


class _AgentImageLifecycle:
    """A3 adapter for the project image task; owns attribution, not generation."""

    def __init__(self, project_id, task_id, node_id, result_id, context, build_result, *, legacy_images=(),
                 validate_start=None, approved_provider=None, plan=None, retry_caller=None,
                 retry_parent=None, retry_models=(), retry_batch_id=None):
        self.failure_policy = bool(context and context.tool != 'canvas.batch_edit')
        self.plan, self.retry_caller = plan, retry_caller
        self.retry_parent, self.retry_models, self.retry_batch_id = retry_parent, retry_models, retry_batch_id
        self.source_at_start = self.result_at_start = self.group_at_start = None
        self.project_id, self.task_id, self.result_id = project_id, task_id, result_id
        self.validate_start = validate_start
        self.approved_provider = approved_provider
        self.legacy_images = legacy_images
        self.store = _app_main.FIREFLY_PROJECT_STORE
        self.context, self.build_result = context, build_result
        self.metadata = {'source_node_id': node_id, 'op_id': context.op_id if context else None}
        self.guarded = set()
        self.event = {'op_id': self.metadata['op_id'], 'node_id': result_id,
                      'persisted': False, 'pending': False, 'updated_at': None, 'listener': self.persisted}
        self.registered = self.start_error = self.failure_record = None
        self.registration_started = False
        self.archived = []

    def persisted(self, pid, canvas):
        if pid == self.project_id and (self.event['persisted'] or any(n.get('id') == self.result_id for n in canvas.get('nodes', []))):
            from app.routers.api_project_library import canvas_has_project_image_pending
            self.event.update(persisted=True, pending=canvas_has_project_image_pending(canvas, self.task_id),
                              updated_at=canvas.get('updated_at'))

    async def register(self, task_record):
        from app.services.agent_ops_async import run_blocking_to_completion

        def create_result():
            canvas = self.store.canvas(self.project_id)
            return self.store.apply_mutations(self.project_id, self.build_result(canvas), base_revision=canvas['revision'])

        def register():
            with self.store._lock:
                validate_pending(self.project_id, self.store.canvas(self.project_id), admit=True)
                if self.validate_start is not None:
                    self.validate_start(self.task_id, self.result_id)
                return register_with_refs()

        def register_with_refs():
            if not self.legacy_images:
                return register_result()
            with self.store._lock:
                canvas = self.store.canvas(self.project_id)
                planned = {image.asset_id for image in self.legacy_images}
                self.store.check_image_reference_files(self.project_id, set(task_record['referenceAssetIds']) - planned)
                imported = archive_legacy_images(_app_main, self.store, self.project_id, canvas, self.legacy_images)
                if imported:
                    self.metadata[AUTO_ARCHIVED_REFS] = sorted(set(imported))
                    task_record.update(self.metadata)
                return register_result()

        def register_result():
            if self.failure_policy:
                self.register_lineage(task_record)
            self.registration_started = True
            batch = _BATCH_GENERATION.get()
            if self.context:
                self.store.add_persisted_canvas_listener(self.persisted)
                if batch is not None:
                    with self.store._lock:
                        batch['tokens'].add(self.task_id)
                        for op_id in batch['ids']:
                            set_agent_generation_running(self.store, self.project_id, op_id, self.task_id, True)
                if batch is not None and batch['root_exists']:
                    _generation_child(self.store, self.project_id, self.context, self.task_id,
                                      self.guarded, create_result, lifecycle=self.event)
                else:
                    _generation_mutation(self.store, self.project_id, self.context, self.task_id,
                                         self.guarded, create_result)
            else:
                with self.store._lock:
                    create_result()
            with self.store._lock:
                if self.failure_policy:
                    canvas = self.store.canvas(self.project_id)
                    self.result_at_start = _snapshot_node(canvas, self.result_id)
                    self.group_at_start = next((n['id'] for n in canvas['nodes']
                                                if n.get('type') == NODE_TYPE_GROUP and self.result_id in n.get('items', [])), None)
                self.registered = self.write('begin_image_generation', self.project_id, self.result_id,
                                             task_record, base_revision=self.store.canvas(self.project_id)['revision'])
            return self.registered

        return await run_blocking_to_completion(register)

    def register_lineage(self, task_record):
        from app.services import agent_batch, agent_generation_retry as retry

        canvas = self.store.canvas(self.project_id)
        self.source_at_start = _snapshot_node(canvas, self.metadata['source_node_id'])
        caller = self.retry_caller or agent_batch.Caller(self.project_id, self.context.agent_node_id)
        if not any(n.get('id') == caller.actor and n.get('type') == NODE_TYPE_AGENT for n in canvas['nodes']):
            return
        scope = retry.scope_key(caller, canvas)
        retry.claim(self.store, self.project_id, self.task_id, parent=self.retry_parent)
        self.metadata.update(retry_scope=scope, retry_source=retry.source_identity(self.source_at_start),
                             retry_root_id=self.retry_parent[0] if self.retry_parent else self.task_id,
                             retry_parent_id=self.retry_parent[1] if self.retry_parent else None,
                             retry_index=self.retry_parent[2] + 1 if self.retry_parent else 0,
                             retry_models=list(self.retry_models), retry_batch_id=self.retry_batch_id,
                             retry_refs=retry.ref_stamp(self.plan.reference_files),
                             retry_provider=agent_batch._digest(self.plan.provider))
        task_record.update(self.metadata)

    def failure_secrets(self, prompt):
        from app.services.generation_failures import sensitive_provider_values
        return (prompt, *sensitive_provider_values(self.approved_provider or self.plan.provider))

    def reconcile_projection(self, opened, durable):
        from app.services.agent_generation_retry import source_identity
        from app.services.generation_failures import describe_failure
        if not self.failure_policy or not durable or not self.source_at_start:
            return opened
        record = durable.get('history_record') or {}
        success = durable['status'] == 'succeeded'
        failed = durable['status'] in {'failed', 'timeout'}
        if not (failed or success):
            return opened
        store, project = self.store, self.project_id

        def projection():
            current = store.canvas(project)
            by_id = {n['id']: n for n in current['nodes']}
            mutations = []
            source = by_id.get(self.source_at_start['id'])
            if (source and source_identity(source) == source_identity(self.source_at_start)
                    and _node_prompt_fields(source) == _node_prompt_fields(self.source_at_start)
                    and source.get('runSettings') == self.source_at_start.get('runSettings')
                    and all(source.get(k) == self.source_at_start.get(k) for k in ('agentStatus', 'lastFailure'))):
                update = {**source, 'agentStatus': 'succeeded' if success else 'failed'}
                if success:
                    update.pop('lastFailure', None)
                else:
                    update['lastFailure'] = record.get('failure') or describe_failure(None, '')['failure']
                if update != source:
                    mutations.append({'op': 'upsert_node', 'node': update})
            if failed and not record.get('outputs'):
                mutations.extend(self.empty_result_cleanup(current, by_id))
            if mutations:
                store.apply_mutations(project, mutations, base_revision=current['revision'])
            return {'canvas': store.canvas(project)}

        return _generation_child(store, project, self.context, self.task_id, self.guarded, projection)

    def empty_result_cleanup(self, canvas, by_id):
        result = by_id.get(self.result_id)
        if not result or not self.result_at_start or result.get('pendingTasks') or self.archived:
            return []
        # Compare the whole card, except fields written by begin/end_image_generation.
        task_fields = {'pending', 'running'}
        if ({k: v for k, v in result.items() if k not in task_fields}
                != {k: v for k, v in self.result_at_start.items() if k not in task_fields}):
            return []

        def references(value):
            if isinstance(value, dict):
                return any(references(v) for v in value.values())
            if isinstance(value, list):
                return any(references(v) for v in value)
            return value == self.result_id

        mutations = []
        for node in by_id.values():
            if node is result:
                continue
            if (node['id'] == self.group_at_start and node.get('type') == NODE_TYPE_GROUP
                    and (node.get('items') or []).count(self.result_id) == 1):
                node = {**node, 'items': [i for i in node['items'] if i != self.result_id]}
                mutations.append({'op': 'upsert_node', 'node': node})
            if references(node):
                return []
        if references(canvas.get('connections', [])) or any(
            references(record) for record in canvas.get('history', [])
            if isinstance(record, dict) and record.get('task_id') != self.task_id
        ):
            return []
        return mutations + [{'op': 'remove_node', 'node_id': self.result_id}]

    def write(self, method, *args, **kwargs):
        def apply():
            return getattr(self.store, method)(*args, **kwargs)
        if self.context:
            return _generation_child(self.store, self.project_id, self.context, self.task_id, self.guarded, apply)
        canvas = apply()
        self.store.flush(self.project_id)
        return canvas

    async def mutate(self, method, *args, **kwargs):
        from app.services.agent_ops_async import run_blocking_to_completion
        if method == 'fail_image_generation':
            self.failure_record = args[-1]
        return await run_blocking_to_completion(self.write, method, *args, **kwargs)

    async def effect(self, description):
        from app.services.agent_ops_async import run_blocking_to_completion
        if self.context:
            await run_blocking_to_completion(_generation_child, self.store, self.project_id, self.context,
                                             self.task_id, self.guarded, lambda: None, effects=(description,))

    async def archive(self, archive, *args):
        await self.effect('generated media archive attempted')
        async def save():
            item = await archive(*args)
            self.archived.append(item)
            return item
        return await _generation_archive_to_completion(save())

    async def start_event(self):
        if self.context:
            try:
                await _generation_archive_to_completion(_generation_event(self.project_id, self.event, 'start'))
            except (Exception, asyncio.CancelledError) as exc:
                self.start_error = self.start_error or exc
        batch = _BATCH_GENERATION.get()
        if batch is not None:
            batch['tasks'].append({'node_id': self.metadata['source_node_id'], 'task_id': self.task_id})

    def update(self, **fields):
        with CANVAS_TASK_LOCK:
            task = CANVAS_TASKS.get(self.task_id)
            if task is not None:
                task.update(fields, updated_at=time.time())

    async def settle(self, error, *, registration=False):
        from app.routers.api_project_library import recover_project_image_task, durable_project_image_result
        from app.services.agent_ops_async import run_blocking_to_completion
        cancelled = isinstance(error, asyncio.CancelledError)
        if registration:
            reason, detail = 'registration_failed', f'任务注册未完成，未调用在线模型：{error}'
        elif cancelled:
            reason, detail = 'cancelled', '任务已取消'
        else:
            reason = 'failure_commit_unknown'
            detail = '生成结果未能确认。' if self.failure_policy else str(error or 'Generation terminal not persisted')
        if registration and self.failure_policy:
            detail = '任务注册未完成，未调用在线模型。'

        def reconcile():
            self.store.flush(self.project_id)
            opened, durable = recover_project_image_task(self.project_id, self.task_id, write=self.write,
                                                         terminal_status='cancelled' if cancelled else 'failed',
                                                         terminal_record=self.failure_record if not cancelled else None,
                                                         outputs=self.archived, termination_reason=reason, termination_error=detail)
            return self.reconcile_projection(opened, durable), durable

        opened, durable = await run_blocking_to_completion(reconcile)
        if self.context:
            if not await _generation_finish(self.store, self.project_id, self.task_id, self.guarded, self.event):
                raise _JournalUnsettled('Generation journal not settled')
        elif durable:
            await _broadcast_canvas_updated(self.project_id, source='agent', updated_at=opened['canvas']['updated_at'])
        if durable:
            result = durable_project_image_result(self.project_id, opened['canvas'], durable)
            with CANVAS_TASK_LOCK:
                current = (CANVAS_TASKS.get(self.task_id) or {}).get('result')
                if current and current.get('history_record', {}).get('status') == durable['status']:
                    result = {**current, 'final_node': result['final_node'], 'project_revision': result['project_revision'],
                              **({'history_record': result['history_record']} if self.failure_policy else {})}
            self.update(status=durable['status'], error=durable['error'], result=result, agent_settled=True)
        elif registration:
            with CANVAS_TASK_LOCK:
                CANVAS_TASKS.pop(self.task_id, None)
        else:
            # #375: the user removed the pending result card. Never resurrect it.
            self.update(status='cancelled', error='目标结果卡已被移除，生成结果未写回', agent_settled=True)

    async def finalize(self, error, *, registration=False):
        from app.core import logger
        from app.services.agent_ops_context import create_agent_ops_task

        async def recover():
            while True:
                try:
                    await self.settle(error, registration=registration)
                    return
                except Exception as exc:
                    logger.warning('Agent generation cleanup task=%s: %s', self.task_id, exc)
                    await asyncio.sleep(1)

        try:
            await self.settle(error, registration=registration)
        except Exception as exc:
            logger.warning('Agent generation cleanup handed off task=%s: %s', self.task_id, exc)
            self.update(status='cleanup_pending', error=f'Generation operation {self.metadata["op_id"]} cleanup pending')
            _track_lifetime_task(create_agent_ops_task(recover()))

    async def registration_failed(self, error):
        if not self.registration_started:
            with CANVAS_TASK_LOCK:
                CANVAS_TASKS.pop(self.task_id, None)
            return
        self.update(**self.metadata)
        error.add_note(f'Generation operation {self.metadata["op_id"]} task {self.task_id}')
        with generation_settlement():
            await _generation_archive_to_completion(self.finalize(error, registration=True))

    def schedule(self, run, *, pre_cancel=False):
        from app.services.agent_ops_context import create_agent_ops_task
        entered = False

        async def supervise():
            nonlocal entered
            entered = True
            error = None
            try:
                if pre_cancel:
                    raise asyncio.CancelledError()
                await run()
            except asyncio.CancelledError as exc:
                error = exc
                if not pre_cancel:
                    raise
            except Exception as exc:
                error = exc
            finally:
                await _generation_archive_to_completion(self.finalize(error))

        coro = supervise()
        try:
            # The task keeps journal attribution, but live approval cannot veto an already paid result.
            with generation_settlement():
                task = create_agent_ops_task(coro)
        except Exception:
            coro.close()
            raise

        def recover_unstarted(done):
            if entered or not done.cancelled():
                return
            with _LIFETIME_LOCK:
                stopping = done.get_loop() in _LIFETIME_STOPPING
            if stopping:
                self.update(status='cleanup_pending', error='Generation interrupted by shutdown; outcome uncertain')
                return
            with CANVAS_TASK_LOCK:
                PROJECT_IMAGE_GENERATION_FUTURES[self.task_id] = self.schedule(run, pre_cancel=True)

        def forget(done):
            with CANVAS_TASK_LOCK:
                if PROJECT_IMAGE_GENERATION_FUTURES.get(self.task_id) is done:
                    PROJECT_IMAGE_GENERATION_FUTURES.pop(self.task_id, None)

        task.add_done_callback(recover_unstarted)
        task.add_done_callback(forget)
        return _track_lifetime_task(task)


def recovered_generation_writer(project_id, durable):
    """Use journal evidence of the admitted task, not current Agent settings."""
    from functools import partial
    from types import SimpleNamespace
    from app.services.agent_ops_store import list_operation_tree
    from firefly_opsdb import OpsDb

    store = _app_main.FIREFLY_PROJECT_STORE
    op_id = durable.get('op_id')
    if not op_id:
        return _app_main.project_store_call if 'source_node_id' in durable and 'op_id' in durable else None
    task_id, result_id = durable['id'], durable['result_node_id']
    with store._lock:
        try:
            family = list_operation_tree(OpsDb(store.root), project_id, op_id)
        except (OSError, sqlite3.Error):
            return None
        root = next((record for record in family if record['op_id'] == op_id and not record['undone']), None)
        if root is None or root['tool'] not in {'generate.trigger', 'generate.retry', 'canvas.batch_edit', 'generate.request_batch'}:
            return None
        if not any(change['id'] == result_id and change['path'] == ['pendingTasks']
                   and any(item.get('taskId') == task_id for item in change['after'].get('value', []))
                   for record in family if not record['undone'] for change in record['after']):
            return None
        context = SimpleNamespace(**root)

    def write(method, *args, **kwargs):
        guarded = {op_id}
        with store._lock:
            set_agent_generation_running(store, project_id, op_id, task_id, True)
            canvas = _generation_child(store, project_id, context, task_id, guarded,
                                       partial(getattr(store, method), *args, **kwargs))
            _generation_release(store, project_id, task_id, guarded)
        return canvas
    return write


async def cancel_shared_agent_image_task(project_id, task_id):
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        future = PROJECT_IMAGE_GENERATION_FUTURES.get(task_id)
        if not task or task.get('project_id') != project_id:
            raise HTTPException(status_code=404, detail='项目生图任务不存在')
        if task['status'] == 'cancelled':
            return {'task_id': task_id, 'status': 'cancelled', 'result': task.get('result')}
        if task['status'] not in {'queued', 'running'} or future is None:
            raise HTTPException(status_code=409, detail='任务已结束，无法取消')
        future.cancel()
    # The supervisor owns the terminal CAS; a cancelled HTTP waiter must not interrupt it.
    while future is not None:
        await asyncio.shield(asyncio.gather(future, return_exceptions=True))
        with CANVAS_TASK_LOCK:
            successor = PROJECT_IMAGE_GENERATION_FUTURES.get(task_id)
            future = successor if successor is not future else None
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS[task_id]
        if current['status'] != 'cancelled':
            raise HTTPException(status_code=409, detail='任务已结束或仍在清理')
        current['cancelled_by_user'] = True
        result = current['result']
        return {'task_id': task_id, 'status': 'cancelled', 'result': result,
                'project_revision': result.get('project_revision') if result else None,
                'result_node_id': current['result_node_id']}


@generate_router.get("/tasks/{task_id}")
async def get_task_status(project_id: str, task_id: str, x_agent_project: str = Header(default="")):
    """Query the status of a generation task."""
    _validate_project(project_id)

    # Check in-memory task store first
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})

    shared = task_id.startswith('agent_img_')
    if shared and task and task.get('project_id') != project_id:
        task = {}
    elif shared and not task:
        from app.routers.api_project_library import recover_project_image_task, durable_project_image_result
        opened, durable = await asyncio.to_thread(recover_project_image_task, project_id, task_id)
        if durable and 'source_node_id' in durable:
            task = {**durable, 'agent_settled': True,
                    'result': durable_project_image_result(project_id, opened['canvas'], durable)}
    if task:
        status = task.get('status', 'unknown')
        result = task.get('result')
        if shared and task.get('type') == 'project-online-image':
            if task.get('agent_settled') is False and status in {'succeeded', 'failed', 'timeout', 'cancelled'}:
                status = 'cleanup_pending'
                result = None
            if status == 'succeeded' and result:
                history = result.get('history_record') or {}
                params = task.get('request_payload') or {}
                result = {**result, 'type': 'online', 'prompt': history.get('executed_prompt') or result['prompt'],
                          'timestamp': (history.get('completed_at') or 0) / 1000,
                          'provider_id': task.get('provider_id'), 'model': task.get('model'),
                          'provider_name': history.get('actual_provider_name') or result.get('provider_name'),
                          'params': {key: params.get(key) for key in ('provider_id', 'model', 'size', 'quality', 'n')},
                          'project_asset_ids': [item['asset_id'] for item in result['image_items']]}
        record = (result or {}).get('history_record') or {}
        failure = record.get('failure')
        if shared and 'details' in record:
            result = {**result, 'history_record': {k: v for k, v in record.items() if k != 'details'}}
        return {
            "task_id": task_id,
            "status": status,
            **({'failure': failure} if failure else {}),
            "type": 'agent-image' if shared else task.get("type", ""),
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


@media_router.post("/upload", dependencies=_AGENT_WRITE)
async def upload_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Upload a media file to the project library."""
    body = await request.json()
    return await _rest_agent_call(request, project_id, "media.upload", body,
                                  lambda: upload_media_args(project_id, body))


async def upload_media_args(project_id, args):
    if "attachment_id" in args:
        from app.routers.api_agent_plan import archive_attachment
        from app.services.agent_refs import require_object
        require_object(args, "media.upload", required=("attachment_id",))
        context = _media_context(project_id)
        return await archive_attachment(project_id, context.agent_node_id if context else "", args["attachment_id"])
    return await upload_media_core(project_id, args.get("file_path"))


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


@media_router.delete("/{asset_id}", dependencies=_AGENT_WRITE)
@resumable
async def delete_media(project_id: str, asset_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """Agent media deletion always passes the safety-floor confirmation gate."""
    _validate_project(project_id)
    await _server_confirm_gate(request, project_id, "media.delete",
                               {"asset_id": asset_id}, f"删除媒体资源 {asset_id}")
    return await _rest_agent_call(request, project_id, "media.delete", {"asset_id": asset_id},
                                  lambda: delete_media_core(project_id, asset_id), agent_node_id)


async def delete_media_core(project_id: str, asset_id: str) -> dict:
    """REST/MCP share recycle semantics after their respective confirmation gates."""
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    context = _media_context(project_id)
    if not context:
        store.delete_media(project_id, asset_id)
        return {"success": True, "asset_id": asset_id}
    import re
    from app.services import agent_ops_media, agent_ops_store
    from app.services.agent_ops_journal import record_sides
    from app.services.agent_ops_async import run_blocking_to_completion
    if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
        raise agent_error(400, "INVALID_ARGUMENT", "Invalid asset ID")
    with store._lock:
        if asset_id not in store._load_media_index(project_id)["items"]:
            return {"success": True, "asset_id": asset_id}
    lifecycle, token = await _media_start(project_id, context, [])
    journal = _journal_for(store)
    committed = uncertain = False

    def recycle():
        nonlocal committed, uncertain
        with store._project_operation_lock(project_id), journal.mutation(project_id) as operation:
            validate_pending(project_id, store.canvas(project_id), admit=True)
            if asset_id not in store._load_media_index(project_id)["items"]:
                return False
            delta = agent_ops_media.deletion_delta(asset_id, context.op_id)
            old, new = record_sides(delta)
            record = {"op_id": context.op_id, "turn_id": context.turn_id,
                      "agent_node_id": context.agent_node_id, "tool": context.tool, "args": context.args,
                      "before": old, "after": new, "touched_node_ids": [],
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "irreversible_effects": [], "undone": False}
            if ((project_id, context.op_id) in journal._entries
                    or agent_ops_store.get_operation(journal._opsdb, project_id, context.op_id) is not None):
                raise ValueError("operation identity already used")
            agent_ops_store._reserve(journal, journal._opsdb, project_id, record)
            try:
                set_agent_generation_running(store, project_id, context.op_id, context.op_id, True)
                lifecycle['guarded'].add(context.op_id)
                store.recycle_agent_media(project_id, asset_id, deletion_id=context.op_id)
                committed = True
                lifecycle['unregistered'][context.op_id] = (record, delta)
                operation.register(record, delta)
                lifecycle['unregistered'].pop(context.op_id)
            except BaseException:
                if committed or store._original_transaction_path(project_id).exists() or any(
                    item.get('agent_deletion_id') == context.op_id
                    for item in store._load_media_trash_index(project_id)['items'] if isinstance(item, dict)
                ):
                    # A durable native effect without a settled journal must not
                    # leave siblings in this logical turn eligible for undo (#442).
                    uncertain = True
                    agent_ops_store.mark_append_failed(journal._opsdb, project_id, record)
                if not committed:
                    agent_ops_store._release(journal, journal._opsdb, project_id, context.op_id)
                raise
            store.flush(project_id)
            status = operation.result()
            if status['status'] not in ('settled', 'append_failed'):
                raise RuntimeError(f"Media deletion journal {status['status']}")
            lifecycle['updated_at'] = store.canvas(project_id)['updated_at']
            return True

    try:
        changed = await run_blocking_to_completion(recycle)
    except BaseException as exc:
        if committed or uncertain:
            raise _media_partial(context, exc) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise agent_error(500, "INTERNAL_ERROR", str(exc),
                          "删除未完成；检查项目回收区与媒体后重试。") from exc
    finally:
        try:
            await _media_end(project_id, lifecycle, token)
        finally:
            _generation_release(store, project_id, context.op_id, lifecycle['guarded'])
    result = {"success": True, "asset_id": asset_id}
    if changed:
        result.update(op_id=context.op_id, touched_node_ids=[])
    return result


# =============================================================================
# Viewport tools
# =============================================================================


@viewport_router.post("/focus", dependencies=_AGENT_WRITE)
async def focus_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Scroll canvas to focus on a node."""
    body = await request.json()
    return await focus_node_core(project_id, body.get("node_id"))


@viewport_router.post("/zoom", dependencies=_AGENT_WRITE)
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
    """Keep an unanswered approval live beyond the synchronous response window.

    Autonomous mode cannot bypass safety-floor tools. A pending receipt does
    not authorize execution; only the original operation may consume approval.
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
    *, allow_automatic: bool = True, transient_session=None, cancel_event=None, confirm_id=None,
    prepared_target_node_ids=None,
) -> dict:
    """Share live, replayable approvals between HTTP, MCP and batch workers."""
    if not node_id:
        raise agent_error(400, "CONFIRM_MISSING_NODE_ID", "node_id is required",
                          "确认门需要发起节点：调用时带 ?agent_node_id=<节点>。")

    from app.services.agent_confirmation import request
    decision = await request(project_id, node_id, tool, params, message, timeout_seconds,
                             allow_automatic=allow_automatic, transient_session=transient_session,
                             cancel_event=cancel_event, confirm_id=confirm_id,
                             prepared_target_node_ids=prepared_target_node_ids)
    if decision.get('approved'):
        # Stale approvals must fail before callers debit their generation quota.
        validate_pending(project_id, _app_main.FIREFLY_PROJECT_STORE.canvas(project_id))
    return decision


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
    """#352：只读预览最多 1568px、1 MiB；视频无可用预览时保留其余媒体的查看能力。"""
    from app.services.canvas_view import clamp_view_max_px, compose_contact_sheet, encode_view_png

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
                continue  # 媒体引用失效时保留其余节点的可查看结果。
            path = str(record.get("path") or "")
            if not path:
                continue
            if kind == "video":
                tile = await asyncio.to_thread(_video_frame_or_none, path, 256)
                if tile is None:
                    continue  # 无法抽帧时保留其余媒体，详细诊断交给 canvas.view_media。
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

    png, (width, height) = await asyncio.to_thread(encode_view_png, sheet, px)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {
        "image_data_url": data_url,
        "width": width,
        "height": height,
        "max_px": px,
        "node_count": len({it["node_id"] for it in items}),
        "items": items,
    }


async def view_media_core(project_id: str, node_id, max_px=None, frames=None) -> dict:
    """#352：图片与视频拼图共用预览预算；尺寸元数据必须匹配最终编码结果。"""
    from app.services.canvas_view import (
        MAX_VIDEO_FRAMES,
        clamp_view_max_px,
        encode_view_png,
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

    png, (width, height) = await asyncio.to_thread(encode_view_png, sheet, px)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {
        "kind": kind,
        "image_data_url": data_url,
        "width": width,
        "height": height,
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


async def confirm_batch_prompt(project_id, actor, tool, count, human_count, *, images=None, preflight=None,
                               target_node_ids=None):
    params = {"node_count": count, "user_edited_count": human_count}
    if preflight is not None:
        params['preflight'] = preflight
    message = f"是否修改 {count} 个节点的提示词？其中 {human_count} 个由用户手动编辑过。"
    if images is not None:
        params["n"] = images
        message += f"本次还会生成 {images} 张图片（消耗外部生成额度）。"
    targets = {'prepared_target_node_ids': target_node_ids} if target_node_ids is not None else {}
    decision = await request_confirmation_core(project_id, actor, tool, params, message,
                                               AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS, **targets)
    if decision.get("approved"):
        return
    if decision.get("reason") == "rejected":
        raise agent_error(409, "CONFIRM_REJECTED", "用户已取消；没有修改任何提示词。",
                          "不要自行重试；如需再次发起请先询问用户。")
    raise agent_error(409, "CONFIRM_PENDING", "未收到用户确认；没有修改任何提示词。",
                      "确认画布标签页仍打开，再询问用户是否需要重新发起。")


async def batch_prompt_edit_core(project_id, actor, parsed, *, binding=None):
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    binding = binding if binding is not None else ("agent", actor)
    if not isinstance(parsed, str):
        items = agent_batch_prompt.preview(store.canvas(project_id), parsed)
        return {"diff_id": agent_batch_prompt.remember(store, project_id, binding, items),
                "items": [item.public() for item in items]}

    from app.services.agent_ops_context import current_agent_ops_context
    context = current_agent_ops_context()
    if not (actor and context and context.project_id == project_id and context.agent_node_id == actor
            and context.tool == "canvas.batch_prompt_edit"):
        raise agent_error(400, "INVALID_ARGUMENT", "Matching Agent context is required to apply the diff")

    items = agent_batch_prompt.take(store, project_id, binding, parsed)
    affected = agent_batch_prompt.changed(items)
    now = _app_main.now_ms()

    def build(canvas):
        return agent_batch_prompt.build_mutations(canvas, items, now)

    build(store.canvas(project_id))
    if affected:
        await confirm_batch_prompt(project_id, actor, "canvas.batch_prompt_edit",
                                   len(affected), sum(item.protected for item in affected),
                                   target_node_ids=[item.node_id for item in affected])
        await _apply_mutations_from_snapshot(project_id, build)
    return _op_result({"applied": [item.node_id for item in affected],
                       "skipped": [{"node_id": item.node_id, "reason": "UNCHANGED"}
                                   for item in items if item.before == item.after]})


def _batch_prompt(node_ids, prompt):
    if not isinstance(node_ids, list) or not node_ids or not all(isinstance(n, str) and n for n in node_ids):
        raise agent_error(400, "INVALID_ARGUMENT", "node_ids 必须是非空节点 ID 数组",
                          "先调用 canvas.list_nodes 获取节点 ID。")
    if len(set(node_ids)) != len(node_ids):
        raise agent_refs.invalid("node_ids must be unique")
    if len(node_ids) > BATCH_EDIT_MAX_NODES:
        raise agent_error(400, "INVALID_ARGUMENT",
                          f"node_ids 单次最多 {BATCH_EDIT_MAX_NODES} 个节点（本次 {len(node_ids)}）",
                          "请拆成多批重试。")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise agent_error(400, "INVALID_ARGUMENT", "prompt 必须是非空字符串",
                          "请填写批量 prompt 后重试。")
    return prompt


def _legacy_batch_input(project_id, node_id, canvas, catalog, prompt, plan, *, source=None):
    from app.services.canvas_image_refs import ReferenceGraph

    graph = ReferenceGraph(canvas, project_id, catalog)
    node = _snapshot_node(canvas, node_id)
    source = node if source is None else source
    return ({key: value for key, value in source.items() if key != 'updated_at'},
            tuple(upstream['id'] for upstream in graph._inputs(node)),
            graph.resolve(node_id, prompt_override=prompt),
            plan.payload.model_dump(exclude={'base_revision'}), plan.final_prompt,
            (plan.route.provider_id, plan.route.model), plan.reference_files, plan.provider)


async def prepare_batch_generation(project_id, node_ids, prompt):
    from copy import deepcopy

    _validate_project(project_id)
    prompt = _batch_prompt(node_ids, prompt)
    store = _app_main.FIREFLY_PROJECT_STORE
    canvas, catalog = await asyncio.to_thread(store.image_reference_snapshot, project_id)
    plans = []
    for node_id in node_ids:
        agent_refs.identifier(node_id, 'node_id')
        plan = await asyncio.to_thread(resolve_generation, project_id, node_id, canvas, catalog,
                                       saved=canvas, prompt_override=prompt)
        node = _snapshot_node(canvas, node_id)
        source = {**node, **agent_batch_prompt.prompt_update(node, prompt, legacy=True)}
        approved_input = _legacy_batch_input(project_id, node_id, canvas, catalog, prompt, plan, source=source)
        plans.append(replace(plan, batch_input=deepcopy(approved_input), provider=deepcopy(plan.provider)))
    return prompt, tuple(plans)


def legacy_batch_prompt_counts(plans, prompt):
    changed = []
    for plan in plans:
        original, text, edited, draft, html, _ = plan.original_prompt
        node = {"type": plan.node_type, "prompt": original, "text": text}
        if draft is not None or html is not None:
            node.update(promptDraftText=draft, promptDraftHtml=html)
        if any(node.get(key) != value for key, value in
               agent_batch_prompt.prompt_update(node, prompt, legacy=True).items()):
            changed.append(edited is True)
    return len(changed), sum(changed)


async def batch_edit_core(project_id: str, node_ids, prompt, agent_node_id: str = "", *, plans=None) -> dict:
    _validate_project(project_id)
    prompt = _batch_prompt(node_ids, prompt)
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

    def conflict():
        return agent_error(409, "CANVAS_CONFLICT", "Canvas changed before the batch write",
                           "画布已改变；检查后重新请求整批确认。")

    try:
        from firefly_projects import RevisionConflict

        # The same revision that produced every source update must still hold
        # after the start event; do not rebase an already confirmed batch.
        canvas = store.canvas(project_id)
        nodes = {node['id']: node for node in canvas.get('nodes', [])}
        revision = canvas['revision']
        updated_at = _app_main.now_ms()
        mutations = []
        for index, node_id in enumerate(node_ids):
            plan = plans[index] if plans is not None else None
            node = nodes.get(node_id)
            if plan is not None and (node is None or str(node.get('type', '')) != plan.node_type or
                                     _node_prompt_fields(node) != plan.original_prompt):
                raise conflict()
            if node is None:
                node = _snapshot_node(canvas, node_id)
            update = agent_batch_prompt.prompt_update(node, prompt, legacy=True)
            if any(node.get(key) != value for key, value in update.items()):
                mutations.append({'op': 'upsert_node', 'node': {**node, **update, 'updated_at': updated_at}})

        def apply():
            try:
                if mutations:
                    return store.apply_mutations(project_id, mutations, base_revision=revision)
                if store.canvas(project_id)['revision'] != revision:
                    raise conflict()
            except RevisionConflict as exc:
                raise conflict() from exc

        if context:
            lifecycle = await _media_start(project_id, context, node_ids)
            try:
                await run_blocking_to_completion(_generation_mutation, store, project_id, context,
                                                 context.op_id, guarded, apply)
            finally:
                await _media_end(project_id, *lifecycle)
        elif await run_blocking_to_completion(apply):
            await _broadcast_canvas_updated(project_id, source='agent')

        for index, node_id in enumerate(node_ids):
            plan = plans[index] if plans is not None else None
            admission = {}
            if plan is not None and plan.batch_input is not None:
                def validate_start(_task_id, _result_id, *, node_id=node_id, plan=plan):
                    # Child result cards change the canvas; only this child's approved inputs
                    # must still match under the lifecycle's persistence lock.
                    try:
                        current_canvas, catalog = store.image_reference_snapshot(project_id)
                        current = resolve_generation(project_id, node_id, current_canvas, catalog,
                                                     saved=current_canvas, prompt_override=prompt,
                                                     providers=_app_main.load_api_providers(read_only=True))
                        actual = _legacy_batch_input(project_id, node_id, current_canvas, catalog, prompt, current)
                    except (HTTPException, ProjectStoreError, OSError, ValueError) as exc:
                        raise conflict() from exc
                    if actual != plan.batch_input:
                        raise conflict()
                admission = {'validate_start': validate_start, 'approved_provider': plan.provider}
            triggered = await trigger_generation_core(project_id, node_id, agent_node_id=agent_node_id,
                                                       plan=plan, **admission)
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
        if context:
            release_batch_journal(store, project_id, context.op_id, batch, guarded)
        _BATCH_GENERATION.reset(token)


def release_batch_journal(store, project_id, root, batch, guarded):
    if _generation_release(store, project_id, root, guarded):
        return
    from app.services.agent_ops_async import run_blocking_to_completion
    from app.services.agent_ops_context import create_agent_ops_task
    from app.core import logger

    journal = _journal_for(store)

    def retry_registration():
        with store._lock:
            for op_id, (record, delta) in list(batch['unregistered'].items()):
                _batch_register_reserved(journal, project_id, record, delta)
                batch['unregistered'].pop(op_id)
                store._agent_generation_unregistered.discard((project_id, op_id))
            store.flush(project_id)

    async def recover():
        while True:
            try:
                await run_blocking_to_completion(retry_registration)
                if _generation_release(store, project_id, root, guarded):
                    return
            except (OSError, sqlite3.Error) as exc:
                logger.warning('batch journal retry op=%s: %s', root, exc)
            await asyncio.sleep(1)

    _track_lifetime_task(create_agent_ops_task(recover()))


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


@router.post("/edit_erase", dependencies=_AGENT_WRITE)
@resumable
async def canvas_edit_erase(project_id: str, request: Request, agent_node_id: str = Query(default=""), x_agent_project: str = Header(default="")):
    """#290 canvas.edit_erase: 按 prompt 擦除节点图选区内容。
    #357：agent 调用经服务端确认门。"""
    body = await request.json()
    await _server_confirm_gate(request, project_id, "canvas.edit_erase", body,
                               f"擦除节点 {body.get('node_id')} 选区图像并覆写节点图（原资源保留在资源库）")
    return await _rest_agent_call(request, project_id, "canvas.edit_erase", body,
                                  lambda: erase_node_image_core(project_id, body.get("node_id"), body.get("rect"), body.get("prompt")), agent_node_id)


# ── #357：服务端确认门（ADR 0022 实化门从客户端/网关移到统一执行入口）──────────
# #456：同一 Agent 写入 URL 不再兼作无头直写路径。MCP 网关仍共用 request_confirmation_core。
# 超时 >60s（review 口径：pi 写操作的等待必须大于旧 60s 门槛）。
AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS = 90.0


class AgentConfirmationDenied(Exception):
    """确认门拒绝/超时（REST 层转 409；MCP 层转 McpToolError）。"""


async def _server_confirm_gate(request: Request, project_id: str, tool: str,
                               args: dict, message: str) -> None:
    require_agent_project(request)
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
    raise agent_error(409, "CONFIRM_PENDING", f"操作仍在等待确认：{tool}",
                      "结束响应并等待系统确认，不要重复询问或重复调用。", details=decision)
