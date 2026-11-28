"""Agent nodes API: session lifecycle, WebSocket streaming, command sending."""
import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.core import ensure_agent_same_origin, ensure_same_origin_request, origin_from_url
from app.constants import NODE_TYPE_AGENT
from app.services.agent_host import (
    agent_host_manager, InvalidEventsCursor, StaleEventsCursor, CorruptEventsLog,
    PI_VERSION_ERROR_PREFIX, validate_events_identifiers,
)
from app.services.agent_errors import agent_error, translate_detail
from app.services.agent_message_refs import resolve_node_refs
from app.services.agent_attachments import attachment_root, resolve_attachments, save_attachment
from app.services.agent_node_host_settings import AgentHostTransaction
from app.routers.api_agent_tools import (
    _find_node, _snapshot_node, _apply_mutations, _broadcast_canvas_updated,
    _open_canvas, reset_generation_counts,
)

# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

# #240：agent-nodes 全部端点（含 WebSocket stream）接入同源/Host 守卫。
router = APIRouter(prefix="/api/projects/{project_id}/agent-nodes", tags=["agent-nodes"], dependencies=[Depends(ensure_agent_same_origin)])

# #335: 未知 agentCli 不得静默回落 pi——create 入口直接 400 拒绝。
AGENT_CLI_WHITELIST = ("pi", "codex")


def _pi_start_error(session) -> str:
    error = getattr(session, "last_error", "")
    return error if isinstance(error, str) and error.startswith(PI_VERSION_ERROR_PREFIX) else ""


def _validated_agent_cli(node: dict) -> str:
    """Empty/missing agentCli → default pi; explicit unknown value → 400 (#335)."""
    raw = str(node.get("agentCli") or "").strip()
    if not raw:
        return "pi"
    if raw not in AGENT_CLI_WHITELIST:
        raise HTTPException(status_code=400, detail={
            "error": "unknown agent_cli",
            "agent_cli": raw,
            "supported": list(AGENT_CLI_WHITELIST),
        })
    return raw


# #354：autonomy 合法值（与 :358 set_agent_autonomy 的校验同源）。
AGENT_AUTONOMY_WHITELIST = ("cautious", "standard", "autonomous")


def _host_transaction(project_id: str, node_id: str):
    return AgentHostTransaction(agent_host_manager, project_id, node_id, find_node=_find_node,
                                update_settings=_update_node_settings, now_ms=_app_main.now_ms,
                                broadcast_settings=_broadcast_canvas_updated)


def _apply_node_host(project_id: str, node_id: str, session) -> None:
    """Hydrate canonical host/config/binding without spawning or losing listeners."""
    if session.is_running:
        return
    try:
        node = _find_node(project_id, node_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return  # Legacy session-only callers do not acquire a canvas identity.
    _host_transaction(project_id, node_id).hydrate(session, node)


def _settings_user_edit(body: dict) -> bool:
    """Settings accept an explicit author flag, never client ownership or time."""
    reserved = {"createdBy", "agentNodeId", "userTouchedAt", "promptEditedByUser",
                "created_at", "updated_at", "createdAt", "updatedAt"}
    if reserved.intersection(body):
        raise agent_error(400, "INVALID_ARGUMENT", "Ownership and timestamps are server-owned",
                          "Remove ownership and timestamp fields; use user_edit=true for a manual change.")
    user_edit = body.get("user_edit", False)
    if type(user_edit) is not bool:
        raise agent_error(400, "INVALID_ARGUMENT", "user_edit must be a boolean",
                          "Omit user_edit or send the JSON boolean true or false.")
    return user_edit


async def _update_node_settings(project_id: str, node_id: str, build_fields, *, user_edit: bool = False,
                                trusted_agent_bindings: bool = False):
    """Commit against the latest snapshot; notify separately, outside compensation.

    The generic canvas mutation helper awaits broadcast after committing. A
    cancellation there must not look like a failed CAS to the host transaction.
    """
    from firefly_projects import RevisionConflict

    _open_canvas(project_id)  # Preserve project-not-found behavior before the CAS loop.
    store = _app_main.FIREFLY_PROJECT_STORE
    apply = store.apply_agent_host_mutations if trusted_agent_bindings else store.apply_mutations
    for attempt in range(8):
        try:
            canvas = store.canvas(project_id)
            revision = int(canvas.get("revision") or 0)
            node = _snapshot_node(canvas, node_id)
            fields = build_fields(node)
            if all(key in node and node[key] == value for key, value in fields.items()):
                return False
            updated = {**node, **fields, "updated_at": _app_main.now_ms()}
            if user_edit:
                updated["userTouchedAt"] = updated["updated_at"]
            apply(project_id, [{"op": "upsert_node", "node": updated}], base_revision=revision)
            return True
        except RevisionConflict:
            if attempt == 7:
                raise agent_error(409, "CANVAS_CONFLICT", "Canvas mutation failed after retries",
                                  "画布并发冲突（已自动重试 8 次）；先调用 canvas.list_nodes 查看最新状态再重新提交。")
        except HTTPException:
            raise
        except Exception as exc:
            raise agent_error(409, "CANVAS_CONFLICT", str(exc),
                              "画布写入冲突；先调用 canvas.list_nodes 查看最新状态再重试。") from exc


async def _update_node_field(project_id: str, node_id: str, field: str, value: Any, *, user_edit: bool = False):
    if await _update_node_settings(project_id, node_id, lambda _node: {field: value}, user_edit=user_edit):
        await _broadcast_canvas_updated(project_id)


def _settings_canvas(project_id: str, *, allow_missing: bool = False):
    """Return a fresh coherent snapshot; only session-only models allow absence."""
    try:
        return _open_canvas(project_id)
    except HTTPException as exc:
        if not allow_missing or exc.status_code != 404:
            raise
        return None


async def _upsert_agent_canvas_node(project_id: str, node_id: str, agent_cli: str, *, model: str = "") -> None:
    """#332: agent-nodes create must persist the node into canvas.json——此前
    API 建的节点只存在于运行内存，进程重启整体蒸发、agent-events 成孤儿。
    已存在的节点绝不动（位置/字段以画布为准）；缺失时写入最小形状，走既有
    canvas 变更通道带 revision+broadcast。
    评审修正：项目不存在必须 404 原样上报（不得被吞成 409）；幻影节点错位
    网格摆放（不再全部堆原点）；spawn 失败时节点保留属预期——卡片可再启动，
    丢节点才是 #332 的病根。"""
    canvas = _open_canvas(project_id)
    if any(isinstance(n, dict) and str(n.get("id", "")) == node_id for n in canvas.get("nodes", [])):
        return
    slot = len(canvas.get("nodes", []))
    now = _app_main.now_ms()
    await _apply_mutations(project_id, [{"op": "upsert_node", "node": {
        "id": node_id,
        "type": NODE_TYPE_AGENT,
        "kind": "agent",
        "title": f"Agent {node_id}",
        "x": 80 + (slot % 10) * 80,
        "y": 80 + (slot // 10) * 80,
        "agentCli": agent_cli,
        "model": model,
        "created_at": now,
        "updated_at": now,
    }}])


@router.post("")
async def create_agent_node(
    project_id: str,
    node_id: str = Body(...),
    model: str = Body(default=""),
    autonomy: str = Body(default=""),
    agent_cli: str = Body(default="", alias="agentCli"),
    x_agent_project: str = Header(default=""),
):
    async with agent_host_manager.node_lock(project_id, node_id):
        return await _create_agent_node_locked(project_id, node_id, model, autonomy, agent_cli, x_agent_project)


async def _create_agent_node_locked(project_id: str, node_id: str, model: str,
                                    autonomy: str, agent_cli: str, x_agent_project: str):
    """Create or resume an agent node session.

    If model/autonomy are not provided, reads them from the node's attributes.
    """
    # #343：创建入口严格校验——node_id/model 来自请求 body（可被 agent 工具链
    # 间接写入），不合白名单直接 400，先于任何会话/落盘/spawn。
    from app.services.agent_host import validate_spawn_arg as _v343
    try:
        _v343("node_id", node_id)
        _v343("model", model, model=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Read node attributes if not explicitly provided
    if not model:
        try:
            node = _find_node(project_id, node_id)
            model = node.get("model", "") or ""
        except HTTPException:
            pass  # Node not found or not yet created; use default

    if not autonomy:
        try:
            node = _find_node(project_id, node_id)
            autonomy = node.get("autonomy", "") or "standard"
        except HTTPException:
            autonomy = "standard"

    # #335: 节点显式声明了未知 agentCli → 400，先于任何会话/spawn。
    # #332: body 可直接带 agentCli（幻影节点无 canvas 属性可读）；同样先验后落。
    # 评审修正：已有节点与 body agentCli 冲突→400（不得静默无视）；宿主权威=canvas 节点属性。
    host_node = None
    try:
        host_node = _find_node(project_id, node_id)
    except HTTPException:
        pass
    requested_cli = (agent_cli or "").strip()
    if requested_cli:
        _validated_agent_cli({"agentCli": requested_cli})
    if isinstance(host_node, dict):
        node_cli = _validated_agent_cli(host_node)
        if requested_cli and requested_cli != node_cli:
            raise HTTPException(status_code=400, detail={
                "error": "agentCli conflicts with existing node attribute",
                "node_agent_cli": node_cli,
                "body_agent_cli": requested_cli,
                "hint": "POST node /settings to change host, or omit agentCli",
            })
        resolved_cli = node_cli
    else:
        resolved_cli = requested_cli or "pi"
        # #332: 节点不在 canvas 时先落盘（先于 spawn），重启不蒸发。
        await _upsert_agent_canvas_node(project_id, node_id, resolved_cli, model=model)

    session = await agent_host_manager.get_or_create_session(project_id, node_id, model, autonomy)

    # Update session model/autonomy if they were read from node attributes
    if model and not session.model:
        session.model = model
    if autonomy:
        session.autonomy = autonomy
    _apply_node_host(project_id, node_id, session)

    # If not running, spawn it
    if not session.is_running:
        success = await agent_host_manager.spawn_session(session, session.model, session.autonomy)
        if not success:
            raise HTTPException(status_code=500, detail=_pi_start_error(session) or "Failed to start agent session")

    return {
        "success": True,
        "node_id": node_id,
        "session_id": session.session_id,
        "status": session.status,
        "model": session.model or model,
        "autonomy": session.autonomy or autonomy,
        # 评审修正：回显=会话实际宿主（_apply_node_host 后的真值），不是请求值。
        "agentCli": getattr(session, "agent_cli", None) or resolved_cli,
    }


@router.delete("/{node_id}")
async def stop_agent_node(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Explicit stop invalidates pending work even without a native process."""
    from app.services.agent_batch import invalidate
    invalidate(project_id, node_id, strict=True)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    await agent_host_manager.stop_session(session)
    # Recreated nodes must not inherit an old session's generation quota.
    reset_generation_counts(project_id, node_id)
    return {"success": True, "node_id": node_id, "status": "stopped"}


@router.get("/external-cli-snippet")
async def external_cli_snippet(
    project_id: str,
    node: str = Query(default="", max_length=200),
    x_agent_project: str = Header(default=""),
):
    """#325 票 G（spec P5）：外部 CLI 接管命令 snippet。

    基址取 server 运行时实际监听值（resolve_agent_api_base：
    FIREFLY_AGENT_API_BASE → FIREFLY_PORT → 3000，#279 同源要求，禁硬编码）。
    与 P1 的 &node= 绑定共用：外部 CLI 会话绑定到该节点（ADR 0022 三级仲裁）。
    """
    if not node:
        raise HTTPException(status_code=400, detail="node query parameter is required")
    _find_node(project_id, node)  # 404 for missing project/node
    from app.services.agent_host import resolve_agent_api_base
    base = resolve_agent_api_base().rstrip("/")
    mcp_url = f"{base}/api/mcp?project={project_id}&node={node}"
    codex_cmd = f"codex exec -c 'mcp_servers.firefly.url=\"{mcp_url}\"' \"列出画布节点\""
    claude_cmd = f"claude mcp add --transport http firefly \"{mcp_url}\""
    return {
        "success": True,
        "node_id": node,
        "mcp_url": mcp_url,
        "codex_cmd": codex_cmd,
        "claude_cmd": claude_cmd,
    }


@router.get("/{node_id}/status")
async def get_agent_status(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Get the current status of an agent node."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    return {
        "node_id": node_id,
        "status": session.status,
        "session_id": session.session_id,
        "model": session.model,
        "autonomy": session.autonomy,
        "is_running": session.is_running,
        "last_error": _pi_start_error(session),
        "started_at": session._started_at,
        "last_activity": session._last_activity,
        "host": session.agent_cli,
        "requested": {"model": session.model, "effort": session.reasoning_effort},
        "effort_state": session.effort_state,
    }


@router.get("/capabilities")
async def get_agent_capabilities(
    project_id: str,
    request: Request,
    host: str = Query(default="pi"),
    model: str = Query(default=""),
):
    """Read native model capabilities without creating or changing a session."""
    ensure_same_origin_request(request)  # CLI spawn requires a positive same-origin signal.
    _open_canvas(project_id)  # Keep the existing project-not-found contract.
    from app.services.agent_model_capabilities import get_capabilities
    try:
        return await asyncio.to_thread(get_capabilities, host, model)
    except ValueError as exc:
        raise agent_error(400, "INVALID_ARGUMENT", str(exc),
                          "选择 pi/codex 和宿主目录中的模型；留空表示跟随宿主。") from exc


@router.get("/chat-models")
async def list_chat_models(project_id: str):
    """#285：只读列出 pi 的会话模型池（~/.pi/agent/models.json + settings.json）。

    此前 Agent 模型下拉列的是 Firefly 图片生成模型（选中即会话无响应）。
    返回 [{id: "provider/modelId", name, provider, model}] + current（settings 默认）。
    只收会话（chat）模型：pi 模型 type 为 image/classifier 的一律剔除（type 缺省按 chat，
    与 pi provider-composer 一致）——图片模型不得混入。绝不写 ~/.pi。
    FIREFLY_PI_AGENT_DIR 可覆盖目录（测试用）。
    """
    import pathlib
    agent_dir = pathlib.Path(_os.environ.get("FIREFLY_PI_AGENT_DIR", "")) if _os.environ.get("FIREFLY_PI_AGENT_DIR") \
        else pathlib.Path.home() / ".pi" / "agent"
    models: List[Dict[str, str]] = []
    current = ""
    try:
        data = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
        for provider_id, provider in (data.get("providers") or {}).items():
            for item in (provider.get("models") or []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                # #285：图片模型不得混入。pi 的模型 type 只有 "chat"/"image"/"classifier"
                # （见 pi toModelType），且 type 缺省即按 chat 处理（provider-composer 的
                # `entry.type ?? "chat"`）。会话模型池只收 chat：image（如 qwen-image-2.1-pro
                # 图像端点）与 classifier 选中即会话无响应，一律剔除。
                if (item.get("type") or "chat") != "chat":
                    continue
                models.append({
                    "id": f"{provider_id}/{item['id']}",
                    "name": item.get("name") or item["id"],
                    "provider": provider_id,
                    "model": item["id"],
                })
    except (OSError, ValueError):
        models = []
    try:
        settings = json.loads((agent_dir / "settings.json").read_text(encoding="utf-8"))
        if settings.get("defaultProvider") and settings.get("defaultModel"):
            current = f"{settings['defaultProvider']}/{settings['defaultModel']}"
    except (OSError, ValueError):
        pass
    return {"models": models, "current": current}


@router.post("/{node_id}/settings")
async def set_agent_settings(project_id: str, node_id: str, body: dict = Body(...),
                             x_agent_project: str = Header(default="")):
    """Atomically validate/save host + model + effort. Busy wire code: agent_busy."""
    user_edit = _settings_user_edit(body)
    unknown = body.keys() - {"host", "model", "effort", "user_edit", "expected"}
    if unknown:
        raise agent_error(400, "INVALID_ARGUMENT", "Unknown settings fields: " + ", ".join(sorted(unknown)))
    result = await _host_transaction(project_id, node_id).settings(body, user_edit=user_edit)
    return {**result, "canvas": _settings_canvas(project_id)}


@router.post("/{node_id}/model")
async def set_agent_model(project_id: str, node_id: str, body: dict = Body(...),
                          x_agent_project: str = Header(default="")):
    """Legacy model entry delegates to the exact same validated transaction."""
    user_edit = _settings_user_edit(body)
    result = await _host_transaction(project_id, node_id).settings(
        {"model": body.get("model", "")}, user_edit=user_edit, session_only=True)
    return {**result, "canvas": _settings_canvas(project_id, allow_missing=not result["persisted"])}


@router.post("/{node_id}/autonomy")
async def set_agent_autonomy(
    project_id: str,
    node_id: str,
    body: dict = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Set the autonomy level for an agent node.

    Valid values: cautious, standard, autonomous.
    Persists to node attributes. For running sessions, the confirmation gate
    scope changes immediately (next tool call); the system prompt suffix takes
    effect on next spawn.
    """
    user_edit = _settings_user_edit(body)
    autonomy = body.get("autonomy", "")
    if autonomy not in ("cautious", "standard", "autonomous"):
        raise HTTPException(status_code=400, detail=f"Invalid autonomy level: {autonomy}")

    # Persist to node attributes
    await _update_node_field(project_id, node_id, "autonomy", autonomy, user_edit=user_edit)

    # Update session autonomy (affects confirm gate scope immediately)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    session.autonomy = autonomy

    return {
        "success": True,
        "node_id": node_id,
        "autonomy": autonomy,
        "canvas": _settings_canvas(project_id),
    }


async def _input_body(request: Optional[Request], message: Any, images=None) -> dict:
    """Keep legacy Python entrypoints while retaining omitted-vs-null wire fields."""
    body = {"message": message}
    if images is not None:
        body["images"] = images
    if request is not None:
        try:
            raw = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Input body must be valid JSON") from exc
        if isinstance(raw, dict):
            body["message"] = raw.get("message")
            for key in ("refs", "request_id", "images", "attachments"):
                if key in raw:
                    body[key] = raw[key]
    return body


def _prepare_input(project_id: str, node_id: str, input_type: str, body: dict) -> dict:
    """Resolve a fresh, authorized project snapshot before any spawn/send."""
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise agent_error(400, "INVALID_ARGUMENT", "message must be a nonblank string.")
    if "request_id" in body:
        request_id = body["request_id"]
        if (not isinstance(request_id, str) or not request_id.strip()
                or len(request_id) > 128 or not request_id.isprintable()):
            raise agent_error(400, "INVALID_ARGUMENT",
                              "request_id must be a nonblank printable string of at most 128 characters.")
    if input_type == "prompt":
        images = body.get("images", [])
        if not isinstance(images, list) or any(not isinstance(image, str) for image in images):
            raise agent_error(400, "INVALID_ARGUMENT", "images must be an array of strings.")
    canvas = _open_canvas(project_id)
    node = _snapshot_node(canvas, node_id)
    # 'agent' is a persisted legacy type, still supported by the old clients.
    if node.get("type") not in (NODE_TYPE_AGENT, "agent"):
        raise agent_error(400, "INVALID_ARGUMENT", "The input target must be an Agent node.")
    resolved = resolve_node_refs(canvas, body["refs"]) if "refs" in body else resolve_node_refs(canvas)
    if "attachments" in body:
        root = attachment_root(_app_main.FIREFLY_PROJECT_STORE.root, project_id, node_id)
        resolved["attachments"] = resolve_attachments(root, body["attachments"])
    return resolved


async def _send_input(project_id: str, node_id: str, input_type: str, body: dict, session=None, *, admission_guard=None) -> dict:
    # Only admission holds the lock, never the lifetime of inference. Steer,
    # follow-up, abort and confirm stay usable while native RPCs are pending.
    if input_type == "prompt":
        async with agent_host_manager.node_lock(project_id, node_id):
            if admission_guard is not None:
                admission_guard()
            return await _send_input_admitted(project_id, node_id, input_type, body, session)
    return await _send_input_admitted(project_id, node_id, input_type, body, session)


async def _send_input_admitted(project_id: str, node_id: str, input_type: str, body: dict, session=None) -> dict:
    resolved = _prepare_input(project_id, node_id, input_type, body)
    if session is None:
        session = await agent_host_manager.get_or_create_session(project_id, node_id)
    # No new kwargs for legacy inputs/managers without any metadata.
    metadata = {}
    if resolved["node_refs"]:
        metadata.update(node_refs=resolved["node_refs"], node_ref_context=resolved["context"])
    if "request_id" in body:
        metadata["request_id"] = body["request_id"]
    if resolved.get("attachments"):
        metadata["attachment_refs"] = resolved["attachments"]
    try:
        if input_type == "prompt":
            _apply_node_host(project_id, node_id, session)
            if not session.is_running:
                if not await agent_host_manager.spawn_session(session):
                    raise agent_error(500, "INTERNAL_ERROR", _pi_start_error(session) or "Failed to start agent session")
            await agent_host_manager.send_prompt(session, body["message"], body.get("images", []), **metadata)
        else:
            if not session.is_running:
                raise agent_error(400, "INVALID_ARGUMENT", "Agent is not running")
            send = (agent_host_manager.send_steer if input_type == "steer"
                    else agent_host_manager.send_follow_up)
            await send(session, body["message"], **metadata)
    except HTTPException:
        raise
    except Exception as exc:
        raise agent_error(502, "UPSTREAM_MODEL_ERROR", f"Agent input send failed: {exc}") from exc
    accepted = {"success": True, "node_id": node_id, "input_type": input_type,
                "node_refs": resolved["node_refs"]}
    if "request_id" in body:
        accepted["request_id"] = body["request_id"]
    return accepted


@router.get('/{node_id}/questions/{presentation_id}')
async def get_agent_question(project_id: str, node_id: str, presentation_id: str):
    from app.services import agent_present
    return agent_present.question_status(_app_main.FIREFLY_PROJECT_STORE, agent_host_manager,
                                         project_id, node_id, presentation_id)


@router.post('/{node_id}/questions/{presentation_id}/answers')
async def answer_agent_question(project_id: str, node_id: str, presentation_id: str, request: Request):
    from app.services import agent_present
    try:
        body = await request.json()
    except ValueError as exc:
        raise agent_error(400, 'INVALID_ARGUMENT', 'Expected a JSON object') from exc

    async def send(body, guard):
        return await _send_input(project_id, node_id, 'prompt', body, admission_guard=guard)

    return await agent_present.answer(_app_main.FIREFLY_PROJECT_STORE, agent_host_manager,
                                      project_id, node_id, presentation_id, body, send)


@router.post("/{node_id}/attachments")
async def upload_agent_attachment(project_id: str, node_id: str, request: Request):
    canvas = _open_canvas(project_id)
    node = _snapshot_node(canvas, node_id)
    if node.get("type") not in (NODE_TYPE_AGENT, "agent"):
        raise agent_error(400, "INVALID_ARGUMENT", "The attachment target must be an Agent node.")
    root = attachment_root(_app_main.FIREFLY_PROJECT_STORE.root, project_id, node_id)
    return await save_attachment(request, root)


@router.post("/{node_id}/prompt")
async def send_agent_prompt(
    project_id: str,
    node_id: str,
    message: Any = Body(default=None),
    images: Any = Body(default=[]),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Send a prompt; optional body refs/request_id describe this input only."""
    return await _send_input(project_id, node_id, "prompt", await _input_body(request, message, images))


@router.post("/{node_id}/steer")
async def send_agent_steer(
    project_id: str,
    node_id: str,
    message: Any = Body(default=None),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Redirect the turn; accepts a body object or the legacy JSON string."""
    return await _send_input(project_id, node_id, "steer", await _input_body(request, message))


@router.post("/{node_id}/follow_up")
async def send_agent_follow_up(
    project_id: str,
    node_id: str,
    message: Any = Body(default=None),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Queue a follow-up, with optional per-input refs/request_id."""
    return await _send_input(project_id, node_id, "follow_up", await _input_body(request, message))


@router.post("/{node_id}/abort")
async def send_agent_abort(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Abort the agent's current turn."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    await agent_host_manager.send_abort(session)
    return {"success": True, "node_id": node_id}


def _require_human_batch_request(request: Request, project_id: str):
    ensure_same_origin_request(request)
    if request.headers.getlist('X-Agent-Project') != [project_id]:
        raise agent_error(403, 'PROJECT_MISMATCH', 'X-Agent-Project must match the requested project')
    if request.query_params:
        raise agent_error(400, 'INVALID_ARGUMENT', 'Human batch requests do not accept query fields')


@router.get("/{node_id}/batches/{batch_id}/status")
async def get_agent_batch_status(project_id: str, node_id: str, batch_id: str, request: Request):
    _require_human_batch_request(request, project_id)
    from app.services.agent_batch import human_batch_status
    return human_batch_status(project_id, node_id, batch_id)


@router.post("/{node_id}/batches/{batch_id}/skip")
@router.post("/{node_id}/batches/{batch_id}/resume")
@router.post("/{node_id}/batches/{batch_id}/retry")
async def recover_agent_batch(project_id: str, node_id: str, batch_id: str, request: Request):
    from app.services import agent_batch
    _require_human_batch_request(request, project_id)
    try:
        body = await request.json()
    except ValueError:
        raise agent_error(400, 'INVALID_ARGUMENT', 'Expected a JSON object') from None
    action = request.url.path.rsplit('/', 1)[-1]
    if action == 'retry':
        return await agent_batch.human_retry_batch_item(project_id, node_id, batch_id, body)
    if action == 'resume':
        return agent_batch.human_resume_batch(project_id, node_id, batch_id, body)
    return agent_batch.human_skip_batch_item(project_id, node_id, batch_id, body)


@router.get("/{node_id}/batches/{batch_id}/preview")
async def get_agent_batch_preview(project_id: str, node_id: str, batch_id: str, request: Request):
    _require_human_batch_request(request, project_id)
    from app.services.agent_batch import human_preview
    return human_preview(project_id, node_id, batch_id)


@router.post("/{node_id}/batches/{batch_id}/preview/edit")
@router.post("/{node_id}/batches/{batch_id}/preview/remove")
async def mutate_agent_batch_preview(project_id: str, node_id: str, batch_id: str, request: Request):
    _require_human_batch_request(request, project_id)
    try:
        body = await request.json()
    except Exception:
        raise agent_error(400, 'INVALID_ARGUMENT', 'Expected a JSON object') from None
    from app.services.agent_batch import human_mutate
    return await human_mutate(project_id, node_id, batch_id, body, request.url.path.rsplit('/', 1)[-1])


@router.post("/{node_id}/confirm")
async def post_agent_node_confirm(
    project_id: str,
    node_id: str,
    request: Request,
    x_agent_project: str = Header(default=""),
):
    """Batch decisions retain their full payload and cannot recover a host."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    confirm_id = str(body.get("confirm_id", ""))
    approved = body.get("approved", False)
    if type(approved) is not bool:
        raise agent_error(400, 'INVALID_ARGUMENT', 'approved must be a boolean')

    from app.services.agent_batch import HUMAN_RETRY_PREFIX, human_confirm
    if confirm_id.startswith(('batch-confirm-', HUMAN_RETRY_PREFIX)) or 'approved_item_ids' in body or 'snapshot_sha256' in body:
        _require_human_batch_request(request, project_id)
        return await human_confirm(project_id, node_id, body)

    session = (agent_host_manager.conversation_session(project_id, node_id) or
               await agent_host_manager.get_or_create_session(project_id, node_id))
    for handler in session.confirm_handlers:
        try:
            await handler(confirm_id, approved)
        except Exception:
            pass

    return {"ok": True, "confirm_id": confirm_id, "approved": approved}


@router.get("/{node_id}/fork-messages")
async def get_fork_messages(
    request: Request,
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Get the list of user messages available for forking."""
    # #342：get_fork_messages 会向存活 `pi --mode rpc` 进程 stdin 写入 RPC 帧
    # （超时时还会 kill 整棵进程树后重生），属副作用 GET，要求显式同源头（Origin/Referer）。
    ensure_same_origin_request(request)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    async with agent_host_manager.node_lock(project_id, node_id):
        transaction = _host_transaction(project_id, node_id)
        if transaction.fields(transaction.current())["agentCli"] != "pi" or session.agent_cli != "pi":
            raise transaction.error(400, "agent_branch_unsupported", "Branches are supported only by the Pi host.")
        await transaction.idle(session)
        messages = await agent_host_manager.get_fork_messages(session)
        return {"success": True, "node_id": node_id, "messages": messages}


@router.get("/{node_id}/events")
async def get_agent_node_events(
    project_id: str, node_id: str, since: int = Query(0, ge=0), host: Optional[str] = None
):
    """#318 票 C：事件回放日志（seq>since 升序）。刷新/重连重建用；
    前端先挂缓冲→GET→按 seq 去重续 live 流（竞态在客户端消解）。"""
    try:
        validate_events_identifiers(project_id, node_id)
    except InvalidEventsCursor as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if host is None:
        host = _host_transaction(project_id, node_id).fields(_find_node(project_id, node_id))["agentCli"]
    if host not in AGENT_CLI_WHITELIST:
        raise agent_error(400, "INVALID_ARGUMENT", "host must be pi or codex")
    try:
        return agent_host_manager.load_events(project_id, node_id, since, host=host)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"events log read failed: {e}")


@router.get("/{node_id}/events/page")
async def get_agent_node_events_page(
    project_id: str, node_id: str, host: str, cursor: Optional[str] = None,
    turns: int = Query(5, ge=1, le=50),
):
    """Read newest or older retained turns, without changing the legacy API."""
    try:
        return agent_host_manager.load_events_page(project_id, node_id, host, turns, cursor)
    except InvalidEventsCursor as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StaleEventsCursor as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (CorruptEventsLog, OSError) as e:
        raise HTTPException(status_code=500, detail=f"events log read failed: {e}")


def _branch_response(project_id: str, node_id: str, branch_id: str) -> dict:
    # Broadcast may await a later writer. Every branch field must describe
    # this same response snapshot, not the branch record from the CAS attempt.
    canvas = _settings_canvas(project_id)
    node = _snapshot_node(canvas, node_id)
    branches = node.get("branches") or []
    return {"success": True, "node_id": node_id, "canvas": canvas,
            "branches": branches, "current_branch": node.get("current_branch") or "",
            "branch": next((b for b in branches if isinstance(b, dict) and b.get("id") == branch_id), None)}


@router.post("/{node_id}/fork")
async def fork_session(
    project_id: str,
    node_id: str,
    entry_id: str = Body(...),
    name: str = Body(default=""),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Fork the conversation from a specific user message.

    Creates a new branch and persists the branch list to the node's attributes.
    """
    user_edit = _settings_user_edit(await request.json() if request is not None else {})
    if not isinstance(entry_id, str) or not entry_id or not isinstance(name, str):
        raise agent_error(400, "INVALID_ARGUMENT", "entry_id must be non-empty and name must be a string")
    branch_id = await _host_transaction(project_id, node_id).branch(entry_id=entry_id, name=name, user_edit=user_edit)
    return _branch_response(project_id, node_id, branch_id)


@router.post("/{node_id}/switch-branch")
async def switch_branch(
    project_id: str,
    node_id: str,
    branch_id: Any = Body(...),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Switch branches; accept the legacy JSON string as well as an object."""
    body = branch_id if isinstance(branch_id, dict) else {}
    user_edit = _settings_user_edit(body)
    branch_id = body.get("branch_id", "") if body else branch_id
    if not isinstance(branch_id, str) or not branch_id:
        raise agent_error(400, "INVALID_ARGUMENT", "branch_id must be a non-empty string",
                          "Send the ID of an existing branch.")
    await _host_transaction(project_id, node_id).branch(branch_id=branch_id, user_edit=user_edit)
    return _branch_response(project_id, node_id, branch_id)


@router.websocket("/{node_id}/stream")
async def agent_event_stream(
    websocket: WebSocket,
    project_id: str,
    node_id: str,
    host: Optional[str] = None,
):
    """Batch decisions need the HTTP-issued capability, not a JSON project header."""
    if host is not None and host not in AGENT_CLI_WHITELIST:
        await websocket.close(code=1008, reason="host must be pi or codex")
        return
    await websocket.accept()

    async with agent_host_manager.node_lock(project_id, node_id):
        session = await agent_host_manager.get_or_create_session(project_id, node_id)
        _apply_node_host(project_id, node_id, session)

    # Browsing only subscribes; spawning here would spend without a user input (#317).
    async def ws_event_handler(sess, event):
        visible_host = session.agent_cli if host is None else host
        if event.get("host", "pi") != visible_host:
            return  # Legacy hostless frames belong to Pi, never Codex.
        try:
            await websocket.send_json(event)
        except Exception:
            pass

    agent_host_manager.on_event(session, ws_event_handler)

    # Subscribe before the initial frame so live events cannot fall through a connection gap.
    await websocket.send_json({
        "type": "session",
        "session_id": session.session_id,
        "status": session.status,
        "model": session.model,
        "autonomy": session.autonomy,
        "agentCli": session.agent_cli,
        "extSessionId": session.ext_session_id,
        "host": session.agent_cli if host is None else host,
        "read_only": host is not None and host != session.agent_cli,
        "effort_state": session.effort_state,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                command = json.loads(data)
            except json.JSONDecodeError:
                continue

            if not isinstance(command, dict):
                await websocket.send_json({"type": "error", "code": "INVALID_ARGUMENT",
                                           "message": "Agent command must be an object."})
                continue
            cmd_type = command.get("type", "")
            if host is not None and host != session.agent_cli:
                await websocket.send_json({"type": "input_rejected", "code": "agent_host_read_only",
                                           "message": "Inactive host history is read-only.",
                                           "request_id": command.get("request_id")})
                continue

            if cmd_type in ("prompt", "steer", "follow_up"):
                try:
                    accepted = await _send_input(project_id, node_id, cmd_type, command, session)
                except HTTPException as exc:
                    detail = translate_detail(exc.status_code, exc.detail)
                    rejected = {"type": "error", "input_type": cmd_type,
                                "message": detail["message"], "code": detail["code"]}
                    if "request_id" in command:
                        rejected.update(type="input_rejected", request_id=command["request_id"])
                    await websocket.send_json(rejected)
                    continue
                # Acceptance means the host send returned, not merely that WS received data.
                await websocket.send_json({"type": "input_accepted", **accepted})

            elif cmd_type == "abort":
                await agent_host_manager.send_abort(session)

            elif cmd_type == "confirm":
                from app.services.agent_batch import HUMAN_RETRY_PREFIX, websocket_confirm
                confirm_id = command.get("confirm_id", "")
                if (isinstance(confirm_id, str) and confirm_id.startswith(('batch-confirm-', HUMAN_RETRY_PREFIX))
                        or any(key in command for key in ('approved_item_ids', 'snapshot_sha256', 'decision_token'))):
                    try:
                        # Only batch decisions require the browser bridge; legacy WS commands keep their contract.
                        if not (origin_from_url(websocket.headers.get('origin', ''))
                                or origin_from_url(websocket.headers.get('referer', ''))):
                            raise HTTPException(403, 'Batch decisions require a same-origin handshake')
                        result = await websocket_confirm(project_id, node_id, command)
                    except HTTPException as exc:
                        await websocket.send_json({'type': 'error', 'status': exc.status_code,
                                                   **translate_detail(exc.status_code, exc.detail)})
                    else:
                        await websocket.send_json({'type': 'confirm_result', **result})
                    continue
                approved = command.get("approved", False)
                for handler in list(session.confirm_handlers):
                    try:
                        await handler(confirm_id, approved)
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    finally:
        if ws_event_handler in session.event_handlers:
            session.event_handlers.remove(ws_event_handler)
