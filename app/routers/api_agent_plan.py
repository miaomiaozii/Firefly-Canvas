"""REST and shared dispatch for the Agent Excel plan tools (#399)."""
import asyncio
from copy import deepcopy
from io import BytesIO
import os as _os
from pathlib import Path
import sys as _sys
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image, UnidentifiedImageError

from app.core import ensure_agent_same_origin, ensure_same_origin_request
from app.routers import api_agent_tools as tools
from app.services import agent_confirmation as confirmation, agent_plan as plan
from app.services.agent_ops_async import run_blocking_to_completion
from app.services.agent_ops_context import agent_ops_scope, current_agent_ops_context
from app.services.agent_refs import invalid, require_object
from app.services.agent_errors import agent_error, translate_detail

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
            return plan.remember(store, scope, record, digest, sheet, data,
                                 session=manager.conversation_session(project, actor)).response(current)

    preview = plan.get(store, scope, plan_id, args)
    if tool == "plan.edit_preview":
        with store._lock:
            if _app_main.FIREFLY_PROJECT_STORE is not store or _scope(store, manager, project, actor, binding) != scope:
                raise plan.stale()
            records = submitted()
            return plan.edit(store, project, actor, preview, args["ops"], records).response(records)

    return await _apply_preview(store, manager, project, actor, preview, args, applied_by="agent")


async def _apply_preview(store, manager, project, actor, preview, args, *, applied_by):
    from app.services.agent_ops_delta import build_delta, touched_node_ids
    from app.services.agent_ops_journal import _conflicts
    from firefly_projects import RevisionConflict

    context = current_agent_ops_context()
    if not context or (context.project_id, context.agent_node_id, context.tool) != (project, actor, "plan.apply"):
        raise invalid("Matching Agent context is required to apply a preview")
    stale = plan.human_stale if applied_by == "human" else plan.stale
    scope = preview.scope
    binding = scope[2]
    with store._lock:
        try:
            plan.check_apply_ready(store, preview)
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "PLAN_STALE":
                raise stale() from exc
            raise
        session = manager.conversation_session(project, actor)
        if session is None or (preview.session is not None and session is not preview.session):
            raise stale()
        runtime_generation = session.generation
        canvas = store.canvas(project)
        try:
            if (_app_main.FIREFLY_PROJECT_STORE is not store or session._stopping
                    or _scope(store, manager, project, actor, binding, canvas) != scope):
                raise stale()
        except HTTPException:
            raise stale() from None
        revision = canvas["revision"]
        tasks = deepcopy(plan.selected(preview, args))
        if applied_by == "human" and not tasks:
            raise invalid("Select at least one plan task before applying")
        try:
            mutations, missing, result = plan.prepare_mutations(canvas, actor, preview, tasks)
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "PLAN_STALE":
                raise stale() from exc
            raise
        if not tasks:
            return result

        source_path = source_identity = None

        def file_identity(path):
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

        def recheck(current, *, check_digest=True):
            nonlocal source_path, source_identity
            try:
                if (_app_main.FIREFLY_PROJECT_STORE is not store or current["revision"] != revision
                        or manager.conversation_session(project, actor) is not session or session._stopping
                        or session.generation != runtime_generation
                        or _scope(store, manager, project, actor, binding, current) != scope
                        or plan.get(store, scope, preview.plan_id, args) is not preview
                        or plan.selected(preview, args) != tasks):
                    raise stale()
                if applied_by == "human":
                    confirmation.validate_pending(project, current)
                if not check_digest:
                    # Poll identity/metadata only; approval and final CAS still hash the full attachment.
                    if source_path is None or file_identity(source_path) != source_identity:
                        raise stale()
                    return mutations
                records = plan.submitted(manager, project, actor, scope[-2], scope[-1])
                source = plan.attachment(store, project, actor, records, preview.attachment_id)
                path = Path(source["path"])
                identity = file_identity(path)
                if (plan.read_attachment(source) != preview.digest
                        or (source_identity is not None and (path, identity) != (source_path, source_identity))):
                    raise stale()
                source_path, source_identity = path, identity
            except (HTTPException, OSError):
                raise stale() from None
            return mutations

        recheck(canvas)
        if not mutations:
            return {**result, "no_changes": True}
        expected = deepcopy(canvas)
        for mutation in mutations:
            store._apply_mutation(expected, deepcopy(mutation), trusted_agent_bindings=False)
        delta = build_delta(canvas, expected)
        snapshot = deepcopy([{key: task[key] for key in ("task_id", "display_no", "section", "name", "refs")}
                             for task in missing])
        params = {"plan_id": preview.plan_id, "generation": preview.generation, "revision": revision,
                  "source_sha256": preview.digest, "tasks": snapshot}
        lines = [f"{preview.name}：创建 {len(snapshot)} 个方案任务（不会生图或导入媒体）。"]
        for task in snapshot:
            refs = "、".join(f"{ref['name']} [{ref['attachment_id'][-8:]}]" for ref in task["refs"]) or "无"
            lines.append(f"{task['section']} · {task['display_no']} {task['name']} — 参考图：{refs}")
        journal = tools._journal_for(store)
        confirm_id = "confirm-" + uuid4().hex[:12]
        cancel_event = asyncio.Event()
        reservation = plan.reserve_apply(store, preview, confirm_id, cancel_event)

    committed = None
    settlement_error = None
    entered_cas = False

    def record_commit(touched):
        nonlocal committed
        # Repeated replacement proof must not clear a settled delivery or undo warning.
        if committed is not None:
            return
        committed = {**result, "op_id": context.op_id, "touched_node_ids": list(touched)}
        plan.finish_apply(store, preview, reservation, state="applied", applied_by=applied_by, result=committed)

    def persisted(pid, current):
        if entered_cas and pid == project and delta and not _conflicts(current, delta):
            # Replacement, not approval or an in-memory delta, consumes the generation.
            record_commit(touched_node_ids(delta))

    def final_recheck(current):
        nonlocal entered_cas
        recheck(current)
        entered_cas = True
        return mutations

    def settle(error):
        nonlocal committed, settlement_error
        if error is not None:
            settlement_error = error
        journal_status = journal_error = None
        with store._lock:
            try:
                receipts = tools._operation_receipts(store, project, {context.op_id})
                if context.op_id in receipts:
                    record_commit(receipts[context.op_id])
                with journal._lock:
                    entry = journal._entries.get((project, context.op_id))
                    journal_status = entry["status"] if entry else None
                    journal_error = entry["error"] if entry else None
                if committed is not None and journal_status == "append_failed" and settlement_error is None:
                    settlement_error = agent_error(
                        500, "INTERNAL_ERROR", "方案已写入画布，但操作日志未保存，撤销不可用。",
                        "不要重复应用；请检查画布与操作日志。", retryable=False,
                        details={"op_id": context.op_id, "journal_status": journal_status, "error": journal_error},
                    )
                precommit = not entered_cas or (entry is None and store.canvas(project) == canvas)
            except Exception:
                precommit = not entered_cas
            state = "applied" if committed is not None else "ready" if precommit else "unknown"
            if committed is not None and settlement_error is not None:
                detail = translate_detail(getattr(settlement_error, "status_code", 500),
                                          getattr(settlement_error, "detail", str(settlement_error)))
                if journal_status == "append_failed":
                    details = detail.get("details", {})
                    if not isinstance(details, dict):
                        details = {"original_error_details": details}
                    detail["details"] = {**details, "op_id": context.op_id,
                                         "journal_status": journal_status, "journal_error": journal_error}
                settlement_error = HTTPException(getattr(settlement_error, "status_code", 500), {
                    **detail, "retryable": False, "hint": "方案已经应用；不要重复提交。请检查画布、回执和操作日志。",
                })
            plan.finish_apply(store, preview, reservation, state=state, applied_by=applied_by,
                              result=committed, error=settlement_error)
        return committed

    def waiting_guard():
        with store._lock:
            recheck(store.canvas(project), check_digest=False)

    try:
        store.add_persisted_canvas_listener(persisted)
    except BaseException as exc:
        settle(exc)
        raise
    try:
        try:
            with store._lock:
                recheck(store.canvas(project))
            if applied_by == "human":
                decision = await confirmation.request(
                    project, actor, "plan.apply", params, "\n".join(lines), tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS,
                    allow_automatic=False, transient_session=session, cancel_event=cancel_event, confirm_id=confirm_id,
                    waiting_guard=waiting_guard,
                )
            else:
                decision = await tools.request_confirmation_core(
                    project, actor, "plan.apply", params, "\n".join(lines), tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS,
                    allow_automatic=False, cancel_event=cancel_event, confirm_id=confirm_id,
                )
            with store._lock:
                if decision.get("reason") != "pending":
                    plan.finish_confirmation(store, preview, reservation)
                recheck(store.canvas(project))
            if not decision.get("approved"):
                raise agent_error(409, "PLAN_NOT_CONFIRMED", "方案未获批准；没有修改画布或媒体。",
                                  "请先与用户核对任务与参考图，再请求批准。", details={"reason": decision.get("reason")})
            await tools._apply_mutations_from_snapshot(project, recheck, recheck_before_cas=final_recheck)
        except BaseException as exc:
            receipt = settle(exc)
            # Stream/flush errors can wrap a durable commit; only an actual CAS failure is stale.
            if receipt is None and isinstance(exc, HTTPException) and isinstance(exc.__cause__, RevisionConflict):
                raise stale() from exc
            if receipt is not None and settlement_error is not exc:
                raise settlement_error from exc
            raise
        receipt = settle(None)
        if receipt is None:
            error = agent_error(500, "INTERNAL_ERROR", "方案提交结果无法确认。",
                                "请检查画布和操作日志，不要盲目重复应用。", retryable=False,
                                details={"plan_id": preview.plan_id, "generation": preview.generation, "op_id": context.op_id})
            settle(error)
            raise error
        if settlement_error is not None:
            raise settlement_error
        return receipt
    finally:
        store.remove_persisted_canvas_listener(persisted)


@confirmation.resumable
async def _apply_human_preview(project_id, agent_node_id, plan_id, args):
    from app.services.agent_host import agent_host_manager as manager

    # Origin.capture otherwise borrows the live Agent turn even for a turn-less human operation.
    waiting = confirmation._current.get()
    if waiting and waiting.origin:
        if not waiting.origin.valid():
            raise plan.human_stale()
        waiting.origin.turn = None
        waiting.origin.hosted = False
    store = _app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        try:
            preview = plan.get_human(store, project_id, agent_node_id, plan_id, args)
        except HTTPException:
            raise plan.human_stale() from None
    result = await _apply_preview(store, manager, project_id, agent_node_id, preview, args, applied_by="human")
    with store._lock:
        try:
            response = plan.get_human(store, project_id, agent_node_id, plan_id, {}).response({})
        except HTTPException:
            raise plan.human_stale() from None
    return {**response, "no_changes": True, "result": result} if result.get("no_changes") else response


def _human_actor(request, *, allow_generation=False):
    allowed = {"agent_node_id", "generation"} if allow_generation else {"agent_node_id"}
    if request.query_params.keys() - allowed or any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise invalid("Unknown or repeated human preview query fields")
    return plan.text(request.query_params.get("agent_node_id"), "agent_node_id", 128)


@router.get("/previews/{plan_id}", dependencies=[Depends(ensure_same_origin_request)])
async def read_human_preview(project_id: str, plan_id: str, request: Request):
    if request.headers.get("X-Agent-Project") != project_id:
        raise agent_error(403, "PROJECT_MISMATCH", "X-Agent-Project must match the requested project")
    tools._validate_project(project_id)
    actor = _human_actor(request, allow_generation=True)
    args = {}
    if "generation" in request.query_params:
        try:
            generation = int(request.query_params["generation"])
        except ValueError as exc:
            raise invalid("generation must be a positive integer") from exc
        if generation < 1:
            raise invalid("generation must be a positive integer")
        args["generation"] = generation
    store = _app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        return plan.get_human(store, project_id, actor, plan_id, args).response({})


@router.post("/previews/{plan_id}/edits", dependencies=[Depends(ensure_same_origin_request)])
async def edit_human_preview(project_id: str, plan_id: str, request: Request):
    if request.headers.get("X-Agent-Project") != project_id:
        raise agent_error(403, "PROJECT_MISMATCH", "X-Agent-Project must match the requested project")
    tools._validate_project(project_id)
    actor = _human_actor(request)
    try:
        args = await request.json()
    except ValueError as exc:
        raise invalid("Expected a JSON object") from exc
    require_object(args, "human plan edit", required=("generation", "ops"))
    if type(args["generation"]) is not int or args["generation"] < 1:
        raise invalid("generation must be a positive integer")
    store = _app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        preview = plan.get_human(store, project_id, actor, plan_id, args)
        return plan.edit(store, project_id, actor, preview, args["ops"], {}).response({})


@router.post("/previews/{plan_id}/apply", dependencies=[Depends(ensure_same_origin_request)])
async def apply_human_preview(project_id: str, plan_id: str, request: Request):
    if request.headers.getlist("X-Agent-Project") != [project_id]:
        raise agent_error(403, "PROJECT_MISMATCH", "X-Agent-Project must match the requested project")
    tools._validate_project(project_id)
    actor = _human_actor(request)
    try:
        args = await request.json()
    except ValueError as exc:
        raise invalid("Expected a JSON object") from exc
    require_object(args, "human plan apply", required=("generation",))
    if type(args["generation"]) is not int or args["generation"] < 1:
        raise invalid("generation must be a positive integer")
    with (
        agent_ops_scope(project_id=project_id, agent_node_id=actor, turn_id=None, tool="plan.apply",
                        args={"plan_id": plan_id, **args, "applied_by": "human"}),
        confirmation.confirmation_transport(hosted=False),
    ):
        return await _apply_human_preview(project_id, actor, plan_id, args)


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
