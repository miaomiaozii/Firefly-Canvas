"""Paid replay admission; the canvas owns inputs, the tasks sidecar owns spent lineage."""
import asyncio
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import re
import sqlite3

from fastapi import HTTPException

from app.services import agent_refs, agent_image_settings
from app.services.agent_batch import _digest, is_running
from app.services.agent_confirmation import _api
from app.services.agent_errors import agent_error
from app.services.canvas_image_refs import ReferenceGraph
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStoreError
from firefly_withdrawn_refs import REATTACH_HINT, historical_image_retry_hint, withdrawn_assets


_TASK = re.compile(r'agent_img_[a-f0-9]{32}')


def source_identity(node):
    return [node.get(key) for key in ('id', 'type', 'created_at')]


def scope_key(caller, canvas):
    key = caller.key(canvas)
    return [key[0], key[1], list(key[2])] if key[2] else None


def ref_stamp(files):
    # A relocated project must revalidate the actual archived media before paid replay.
    return _digest(files)


def guard_withdrawal(canvas, task, assets=()):
    if historical_image_retry_hint(canvas, task) or withdrawn_assets(canvas).intersection(assets):
        raise agent_error(409, 'REF_REATTACH_REQUIRED', '历史参考图不能安全重放。', REATTACH_HINT)


def _lineage_error():
    return agent_error(409, 'RETRY_LIMIT', 'This attempt is no longer the retryable tip, or its two retries are spent')


def check_tip(store, project, root, tip, attempts):
    if attempts >= 2:
        raise _lineage_error()
    path = OpsDb(store.root).db_path(project)
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute('SELECT tip_id, retries FROM image_task_retry_lineage WHERE root_id=?', (root,)).fetchone()
    except sqlite3.Error as exc:
        raise _lineage_error() from exc
    if row != (tip, attempts):
        raise _lineage_error()


def claim(store, project, task, *, parent=None):
    db = OpsDb(store.root)
    with db.connect(project, create_project_dir=False) as conn, conn:
        if not parent:
            conn.execute('INSERT INTO image_task_retry_lineage VALUES (?, ?, 0)', (task, task))
        else:
            root, tip, attempts = parent
            changed = conn.execute('UPDATE image_task_retry_lineage SET tip_id=?, retries=retries+1 '
                                   'WHERE root_id=? AND tip_id=? AND retries=? AND retries<2',
                                   (task, root, tip, attempts)).rowcount
            if changed != 1:
                raise _lineage_error()


def model_plan(plan, providers, model, *, provider_id='', change=None):
    from app.routers.api_project_library import project_image_request
    wire = plan.payload.model_copy(update=change or {})
    request = project_image_request(wire)
    route = _api()._app_main.route_image_generation(request, providers, provider_id=provider_id, model=model)
    if route.model != model:
        # Routing aliases or resolution variants are not an approval for another identifier.
        raise _model_error()
    provider = next(p for p in providers if p['id'] == route.provider_id)
    return replace(plan, payload=wire, request=request, route=route, provider=provider)


def _model_error():
    return agent_error(409, 'MODEL_NOT_APPROVED', 'The exact model is not approved for this snapshot',
                       '请在确认卡加入该模型，并重新发起生成。')


def aligned_plan(plan, providers):
    api = _api()
    provider = next(p for p in providers if p['id'] == plan.route.provider_id)
    allowed = agent_image_settings.allowed_aspect_ratios(provider, plan.route.model, api._app_main.model_spec_for)
    current = plan.request.aspect_ratio
    if not current or not allowed:
        return None
    ratio = agent_image_settings.nearest_aspect_ratio(current, [a for a in allowed if a != current])
    if ratio == current:
        return None
    change = {'aspect_ratio': ratio}
    if plan.request.width or plan.request.height or plan.payload.size:
        tier = plan.request.resolution
        if tier is None:
            tiers = [t for t in ('1k', '2k', '4k') if current in agent_image_settings._RATIOS
                     and plan.request.size == agent_image_settings.size_for_ratio(current, '', t, {})]
            tier = tiers[0] if len(tiers) == 1 else None
        if tier not in ('1k', '2k', '4k'):
            return None  # Don't invent a pixel tier for explicit/custom dimensions.
        change.update(size=agent_image_settings.size_for_ratio(ratio, '', tier, {}), width=None, height=None)
    return model_plan(plan, providers, plan.route.model, provider_id=plan.route.provider_id, change=change)


def verify_admission(store, project, record):
    from app.services.agent_ops_store import list_operation_tree

    db = OpsDb(store.root)
    if not db.has_db(project) or not record.get('op_id'):
        raise _stale()
    family = list_operation_tree(db, project, record['op_id'])
    if not any(not op['undone'] and op['op_id'] == record['op_id']
               and op['agent_node_id'] == record['retry_scope'][2][0]
               and op['tool'] in {'generate.trigger', 'generate.retry', 'generate.request_batch'} for op in family):
        raise _stale()
    if not any(change['id'] == record['result_node_id'] and change['path'] == ['pendingTasks']
               and isinstance(change['after'].get('value'), list)
               and any(task.get('taskId') == record['task_id'] and task.get('requestPayload') == record['request_payload']
                       and task.get('retry_scope') == record['retry_scope'] for task in change['after']['value'])
               for op in family if not op['undone'] for change in op['after']):
        raise _stale()


def _stale():
    return agent_error(409, 'RETRY_STALE', 'Generation source, attachments, or route changed; start a new request')


def arguments(args):
    agent_refs.require_object(args, 'generate.retry', optional=('task_id', 'node_id', 'model', 'use_snapshot'))
    if ('task_id' in args) == ('node_id' in args) or type(args.get('use_snapshot', True)) is not bool:
        raise agent_refs.invalid('Supply exactly one of node_id or task_id and a boolean use_snapshot')
    for key in ('task_id', 'node_id', 'model'):
        if key in args:
            agent_refs.identifier(args[key], key)
    return args


def prepare(caller, args):
    api = _api()
    project = caller.project
    store = api._app_main.FIREFLY_PROJECT_STORE
    from app.routers.api_project_library import project_image_executed_prompt, project_image_request
    # Use open_project so #460 sees the recovered durable document, not a request or registry snapshot.
    with store._lock:
        try:
            canvas = store.open_project(project)['canvas']
        except ProjectStoreError as exc:
            raise _stale() from exc
        scope = scope_key(caller, canvas)
        history = [item for item in canvas.get('history', []) if isinstance(item, dict)]
        ident = args.get('task_id')
        if ident and not _TASK.fullmatch(ident):
            raise agent_error(404, 'TASK_NOT_FOUND', 'No scoped Agent image task')
        if ident:
            guard_withdrawal(canvas, ident)
            record = next((r for r in reversed(history) if r.get('task_id') == ident), None)
        else:
            node_id = args['node_id']
            record = next((r for r in reversed(history) if node_id in (r.get('result_node_id'), r.get('source_node_id'))
                           and r.get('retry_scope') == scope and scope), None)
            if record is None:
                # Pre-A11 imports still need the #460 reattach response by node.
                record = next((r for r in reversed(history) if node_id in (r.get('result_node_id'), r.get('source_node_id'))), None)
            if record and _TASK.fullmatch(record.get('task_id') or ''):
                guard_withdrawal(canvas, record['task_id'])
        if not record or not scope or record.get('retry_scope') != scope:
            raise agent_error(404, 'TASK_NOT_FOUND', 'No scoped Agent image task')
        if record.get('status') != 'failed' or not record.get('replay_safe') or record.get('outputs') or record.get('upstream_task_id'):
            raise agent_error(409, 'RETRY_UNSAFE', 'Only a settled failed attempt with no output or uncertain submission can be replayed')
        if is_running(project, record.get('retry_batch_id')):
            raise agent_error(409, 'RETRY_BUSY', 'This item still belongs to an active batch')
        parent = (record['retry_root_id'], record['task_id'], record['retry_index'])
        assets = record['request_payload']['reference_asset_ids']
        guard_withdrawal(canvas, record['task_id'], assets)
        check_tip(store, project, *parent)
        try:
            verify_admission(store, project, record)
            node = api._snapshot_node(canvas, record['source_node_id'])
            if source_identity(node) != record.get('retry_source'):
                raise _stale()
            _, catalog = store.image_reference_snapshot(project, include_canvas=False)
            graph = ReferenceGraph(canvas, project, catalog)
            attached = [(asset, role) for _, asset, role in graph.image_positions(graph.resolve(node['id']))]
            roles = record['request_payload']['reference_roles'] or [None] * len(assets)
            if [(asset, role or '') for asset, role in attached] != [(a, r or '') for a, r in zip(assets, roles)]:
                raise _stale()
            files = store.check_image_reference_files(project, assets, fingerprint=True)
            if ref_stamp(files) != record['retry_refs']:
                raise _stale()
            providers = api._app_main.load_api_providers(read_only=True)
            approved = tuple(record.get('retry_models', ()))
            model = args.get('model')
            if model and model != record['actual_model'] and model not in approved:
                raise _model_error()
            if args.get('use_snapshot', True):
                wire = api.ProjectImageGenerationRequest(**record['request_payload'])
                plan = api._PreparedGeneration(project_image_request(wire), wire, None, node['type'],
                                               api._node_prompt_fields(node), final_prompt=project_image_executed_prompt(wire),
                                               reference_files=files)
                selected = model or record['actual_model']
                pinned = record['actual_provider_id'] if selected == record['actual_model'] else ''
                plan = model_plan(plan, providers, selected, provider_id=pinned)
                if pinned and _digest(plan.provider) != record['retry_provider']:
                    raise _stale()
            else:
                plan = api.resolve_generation(project, node['id'], canvas, catalog, saved=canvas,
                                              providers=providers, preflight=True)
                if plan.legacy_images or withdrawn_assets(canvas).intersection(plan.payload.reference_asset_ids):
                    raise agent_error(409, 'REF_REATTACH_REQUIRED', '历史参考图不能安全重放。', REATTACH_HINT)
                if model:
                    plan = model_plan(plan, providers, model)
        except (OSError, ProjectStoreError, ValueError, sqlite3.Error) as exc:
            raise _stale() from exc
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get('code') in ('MODEL_NOT_APPROVED', 'REF_REATTACH_REQUIRED'):
                raise
            raise _stale() from exc
        stamp = _digest((scope, parent, node, attached, plan.reference_files,
                         plan.payload.model_dump(exclude={'base_revision'}), plan.route.to_dict(), plan.provider))
        return node['id'], parent, approved, stamp, deepcopy(plan)


def retry_confirmation_content(caller, args, prepared):
    node, parent, _, _, plan = prepared
    count = plan.request.count
    return (
        {'task_id': parent[1], 'node_id': node, 'n': count, 'model': plan.route.model, 'provider': plan.route.provider_id,
         'use_snapshot': args.get('use_snapshot', True),
         'preflight': _api().generation_confirmation_input(caller.project, node, plan)},
        f'重新生成 {count} 张图片（重新消耗外部额度）\n模型：{plan.route.model} · {plan.route.provider_id}\n{plan.final_prompt}',
    )


async def execute(caller, args):
    from app.services.agent_ops_async import run_blocking_to_completion
    from app.services.agent_host import agent_host_manager as host
    api = _api()
    args = arguments(args)
    prepared = await run_blocking_to_completion(prepare, caller, args)
    session = await host.get_or_create_session(caller.project, caller.actor, recover_history=False)
    params, message = retry_confirmation_content(caller, args, prepared)
    decision = await api.request_confirmation_core(
        caller.project, caller.actor, 'generate.retry', params, message,
        api.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS, transient_session=session,
        cancel_event=asyncio.Event())
    if not decision.get('approved') or session._stopping or host.conversation_session(caller.project, caller.actor) is not session:
        raise agent_error(409, 'CONFIRM_REJECTED' if decision.get('reason') == 'rejected' else 'CONFIRM_PENDING',
                          'Fresh retry confirmation was not granted')
    return await dispatch_confirmed_retry(caller, args, prepared, session)


async def dispatch_confirmed_retry(caller, args, prepared, session, *, guard=None):
    from app.services.agent_host import agent_host_manager as host
    api = _api()
    node, parent, approved, stamp, plan = prepared

    def admit(task_id, result_id):
        if prepare(caller, args)[3] != stamp or session._stopping or host.conversation_session(caller.project, caller.actor) is not session:
            raise _stale()
        if guard is not None:
            guard()
        api.enforce_generation_limit(caller.project, caller.actor, plan.request.count)

    return await api.trigger_generation_core(caller.project, node, caller.actor, plan=plan,
                                             validate_start=admit, approved_provider=plan.provider,
                                             retry_caller=caller, retry_parent=parent, retry_models=approved)
