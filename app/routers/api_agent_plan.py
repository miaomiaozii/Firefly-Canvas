"""REST and shared dispatch for the Agent Excel plan tools (#399)."""
import asyncio
from io import BytesIO
import os as _os
from pathlib import Path
import sys as _sys

from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image, UnidentifiedImageError

from app.core import ensure_agent_same_origin
from app.routers import api_agent_tools as tools
from app.services import agent_plan as plan
from app.services.agent_ops_async import run_blocking_to_completion
from app.services.agent_ops_context import current_agent_ops_context
from app.services.agent_refs import invalid, require_object
from app.services.agent_errors import agent_error

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

router = APIRouter(prefix="/api/projects/{project_id}/plan", tags=["agent-plan"],
                   dependencies=[Depends(ensure_agent_same_origin), Depends(tools.require_agent_project)])


def _scope(store, manager, project, actor, binding, canvas=None):
    return (project, actor, binding, *plan.conversation(store, manager, project, actor, canvas))


async def plan_core(project, actor, args, *, tool, binding="rest"):
    from app.services.agent_host import agent_host_manager as manager

    if tool == "plan.import_excel":
        if not isinstance(args, dict) or args.keys() - {"attachment_id", "sheet"}:
            raise plan.file_not_allowed()
        source = plan.attachment_id(args.get("attachment_id"))
        sheet = plan.text(args["sheet"], "sheet") if "sheet" in args else None
    else:
        edit = tool == "plan.edit_preview"
        require_object(args, tool, required=("plan_id", "ops") if edit else ("plan_id",),
                       optional=("generation",) if edit else ("generation", "task_ids", "layout"))
        plan_id = plan.text(args["plan_id"], "plan_id", 128)
        if not edit and args.get("layout", "by_section") != "by_section":
            raise invalid("Only by_section layout is supported")

    tools._validate_project(project)
    store = _app_main.FIREFLY_PROJECT_STORE
    try:
        scope = _scope(store, manager, project, actor, binding)
    except HTTPException:
        if tool != "plan.import_excel":
            raise plan.stale() from None
        raise

    def submitted():
        return plan.submitted(manager, project, actor, scope[-2], scope[-1])

    if tool == "plan.import_excel":
        records = submitted()
        record = plan.attachment(store, project, actor, records, source)
        if Path(record["name"]).suffix.lower() != ".xlsx":
            raise agent_error(400, "NOT_XLSX", "请提供 .xlsx 方案附件。")

        def load():
            digest, content = plan.read_attachment(record, content=True)
            return digest, plan.parse_workbook(content, sheet)

        digest, data = await asyncio.to_thread(load)
        with store._lock:
            if _app_main.FIREFLY_PROJECT_STORE is not store or scope != _scope(store, manager, project, actor, binding):
                raise plan.stale()
            current = submitted()
            if plan.read_attachment(plan.attachment(store, project, actor, current, source)) != digest:
                raise plan.stale()
            return plan.remember(store, scope, record, digest, sheet, data).response(current)

    preview = plan.get(store, scope, plan_id, args)
    if tool == "plan.edit_preview":
        with store._lock:
            if _app_main.FIREFLY_PROJECT_STORE is not store or _scope(store, manager, project, actor, binding) != scope:
                raise plan.stale()
            records = submitted()
            return plan.edit(store, project, actor, preview, args["ops"], records).response(records)

    context = current_agent_ops_context()
    if not context or (context.project_id, context.agent_node_id, context.tool) != (project, actor, tool):
        raise invalid("Matching Agent context is required to apply a preview")
    session = manager.conversation_session(project, actor)
    runtime_generation = session.generation
    with store._lock:
        canvas = store.canvas(project)
        revision = canvas["revision"]
        mutations, missing, result = plan.prepare_mutations(canvas, actor, preview, plan.selected(preview, args))

    if not mutations:
        return result

    def recheck(canvas):
        # Also run under the store lock after the awaited canvas-update announcement.
        try:
            if (_app_main.FIREFLY_PROJECT_STORE is not store or canvas["revision"] != revision
                    or session.generation != runtime_generation or _scope(store, manager, project, actor, binding, canvas) != scope
                    or plan.get(store, scope, plan_id, args) is not preview):
                raise plan.stale()
            records = submitted()
            source = plan.attachment(store, project, actor, records, preview.attachment_id)
            if plan.read_attachment(source) != preview.digest:
                raise plan.stale()
        except (HTTPException, OSError):
            raise plan.stale() from None
        return mutations

    with store._lock:
        recheck(store.canvas(project))
    snapshot = [{key: task[key] for key in ("task_id", "display_no", "section", "name", "refs")} for task in missing]
    lines = [f"{preview.name}：创建 {len(snapshot)} 个方案任务（不会生图或导入媒体）。"]
    for task in snapshot:
        refs = "、".join(f"{ref['name']} [{ref['attachment_id'][-8:]}]" for ref in task["refs"]) or "无"
        lines.append(f"{task['section']} · {task['display_no']} {task['name']} — 参考图：{refs}")
    decision = await tools.request_confirmation_core(
        project, actor, tool, {"plan_id": plan_id, "generation": preview.generation, "revision": revision,
                              "source_sha256": preview.digest, "tasks": snapshot},
        "\n".join(lines), tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS, allow_automatic=False,
    )
    if not decision.get("approved"):
        raise agent_error(409, "PLAN_NOT_CONFIRMED", "方案未获批准；没有修改画布或媒体。",
                          "请先与用户核对任务与参考图，再请求批准。", details={"reason": decision.get("reason")})
    try:
        await tools._apply_mutations_from_snapshot(project, recheck, recheck_before_cas=recheck)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "CANVAS_CONFLICT":
            raise plan.stale() from exc
        raise
    return tools._op_result(result)


@router.post("/{operation}")
async def call_plan(project_id: str, operation: str, request: Request):
    if operation not in ("import_excel", "edit_preview", "apply"):
        raise HTTPException(404)
    name = f"plan.{operation}"
    if request.headers["X-Agent-Project"] != project_id:
        raise plan.file_not_allowed() if operation == "import_excel" else plan.stale()
    try:
        args = await request.json()
    except ValueError:
        raise plan.file_not_allowed() if operation == "import_excel" else invalid("Expected a JSON object")
    if operation != "import_excel" and not isinstance(args, dict):
        raise invalid("Expected a JSON object")
    actor = request.query_params.get("agent_node_id", "")
    if operation == "apply":
        if not actor:
            raise plan.stale()
        return await tools._rest_agent_call(request, project_id, name, args,
                                            lambda: plan_core(project_id, actor, args, tool=name), actor)
    return await plan_core(project_id, actor, args, tool=name)


async def archive_attachment(project, actor, attachment_id):
    """Separate, user-confirmed media.upload; plan.* never calls this capability."""
    from app.services.agent_host import agent_host_manager as manager

    identifier = plan.attachment_id(attachment_id)
    tools._validate_project(project)
    store = _app_main.FIREFLY_PROJECT_STORE
    context = tools._media_context(project)
    if not context or context.tool != "media.upload" or context.agent_node_id != actor:
        raise plan.file_not_allowed()
    identity = plan.conversation(store, manager, project, actor)
    generation = manager.conversation_session(project, actor).generation

    def resolve():
        if (_app_main.FIREFLY_PROJECT_STORE is not store
                or plan.conversation(store, manager, project, actor) != identity
                or manager.conversation_session(project, actor).generation != generation):
            raise plan.file_not_allowed()
        records = plan.submitted(manager, project, actor, identity[1], identity[2])
        return plan.attachment(store, project, actor, records, identifier)

    record = resolve()
    if not record["type"].startswith("image/"):
        raise plan.ref_not_allowed()
    digest, data = await asyncio.to_thread(plan.read_attachment, record, content=True)
    try:
        with Image.open(BytesIO(data)) as image:
            mime = Image.MIME.get(image.format, "")
            image.verify()
            if not mime.startswith("image/"):
                raise ValueError
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise plan.ref_not_allowed() from None
    decision = await tools.request_confirmation_core(
        project, actor, "media.upload", {"attachment_id": identifier, "sha256": digest},
        f"是否将会话图片 {record['name']} [{identifier[-8:]}] 明确归档到当前项目？",
        tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS, allow_automatic=False,
    )
    if not decision.get("approved"):
        raise agent_error(409, "CONFIRM_REJECTED", "会话图片未获归档批准。")
    guarded = set()
    lifecycle = await tools._media_start(project, context, [])
    try:
        def commit():
            with store._lock:
                if plan.read_attachment(resolve()) != digest:
                    raise plan.file_not_allowed()
                tools._media_root(store, project, context, "Explicit conversation image archive", guarded)
                return store.import_media(project, data, record["name"], mime, plan.archive_source(actor, record))

        media = await run_blocking_to_completion(commit)
    except Exception as exc:
        if guarded:
            raise tools._media_partial(context, exc) from exc
        raise
    finally:
        try:
            if lifecycle:
                await tools._media_end(project, *lifecycle)
        finally:
            if guarded:
                tools._generation_release(store, project, context.op_id, guarded)
    return {"success": True, "asset_id": media["asset_id"], "op_id": context.op_id, "touched_node_ids": []}
