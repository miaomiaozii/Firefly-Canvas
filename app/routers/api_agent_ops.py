"""Project-scoped Agent operation query and conflict-checked undo."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import ensure_agent_same_origin
from app.routers.api_agent_tools import _app_main, _validate_project, agent_generation_is_running
from app.services import agent_ops_undo
from app.services.agent_errors import agent_error

router = APIRouter(prefix="/api/projects/{project_id}/ops", tags=["agent-ops"],
                   dependencies=[Depends(ensure_agent_same_origin)])


def _invalid(message):
    return agent_error(400, "INVALID_ARGUMENT", message, "检查参数后重试。")


def _validate_args(args, *, undo=False):
    allowed = {"op_id", "turn_id"} if undo else {"turn_id", "limit"}
    if not isinstance(args, dict) or set(args) - allowed:
        raise _invalid("参数必须是仅包含允许字段的对象")
    for key in ("op_id", "turn_id"):
        if key in args and (not isinstance(args[key], str) or not args[key].strip()):
            raise _invalid(f"{key} 必须是非空字符串")
    if undo and ("op_id" in args) == ("turn_id" in args):
        raise _invalid("必须且只能提供 op_id 或 turn_id")
    if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 1000):
        raise _invalid("limit 必须是 1..1000 的整数")


async def list_ops_core(project_id, args):
    _validate_args(args)
    _validate_project(project_id)
    records = await asyncio.to_thread(agent_ops_undo.list_ops, _app_main.FIREFLY_PROJECT_STORE,
                                      project_id, turn_id=args.get("turn_id"), limit=args.get("limit", 100))
    return {"operations": records}


async def undo_ops_core(project_id, args):
    _validate_args(args, undo=True)
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    result = await asyncio.to_thread(agent_ops_undo.undo_ops, store, project_id,
                                     op_id=args.get("op_id"), turn_id=args.get("turn_id"),
                                     is_running=lambda pid, op_id: agent_generation_is_running(store, pid, op_id))
    # A retry can persist an inverse already present in memory without reverting it again.
    broadcast_ids = set(result.pop("broadcast_op_ids", [])) | set(result["reverted"])
    if broadcast_ids:
        records = await asyncio.to_thread(agent_ops_undo.list_ops, store, project_id, limit=1000)
        touched = sorted({node_id for record in records if record["op_id"] in broadcast_ids
                          for node_id in record["touched_node_ids"]})
        canvas = store.canvas(project_id)
        await _app_main.manager.broadcast_canvas_updated(
            project_id, int(canvas.get("updated_at") or _app_main.now_ms()), source="agent",
            op_id=args.get("op_id"), touched_node_ids=touched, phase="undo")
    if result["status"] == "conflict":
        raise HTTPException(status_code=409, detail={
            "code": "UNDO_CONFLICT", "message": "画布在操作后已发生变更，撤销未执行。",
            "hint": "查看冲突并只撤销无冲突部分或询问用户。", "retryable": False,
            "details": result,
        })
    if result["status"] == "generating":
        raise agent_error(409, "CONFLICT", "生成仍在进行，撤销未执行。",
                          "等待生成结束后再撤销。", retryable=True, details=result)
    if result["status"] == "not_found":
        raise agent_error(404, "NOT_FOUND", "未找到指定操作或轮次。", "调用 ops.list 查看操作。")
    if result["status"] in ("pending", "error") or result.get("error"):
        raise agent_error(500, "INTERNAL_ERROR", "撤销未完全持久化。",
                          "检查画布和操作状态后再决定是否重试。", details=result)
    return result


@router.get("")
async def list_ops_endpoint(project_id: str, request: Request):
    args = dict(request.query_params)
    if "limit" in args:
        try:
            args["limit"] = int(args["limit"])
        except ValueError:
            raise _invalid("limit 必须是 1..1000 的整数")
    return await list_ops_core(project_id, args)


@router.post("/undo")
async def undo_ops_endpoint(project_id: str, request: Request):
    try:
        args = await request.json()
    except ValueError:
        raise _invalid("请求体必须是 JSON 对象")
    return await undo_ops_core(project_id, args)
