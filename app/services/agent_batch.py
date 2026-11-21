"""A10 in-process admission and scheduling; individual image tasks are durable."""
import asyncio
import contextvars
from copy import deepcopy
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote
from uuid import uuid4
from weakref import WeakValueDictionary

from fastapi import HTTPException

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP, NODE_TYPE_IMAGE, NODE_TYPE_PROMPT
from app.services import agent_refs
from app.services.agent_errors import agent_error
from app.services.agent_ops_async import run_blocking_to_completion
from app.services.agent_ops_context import agent_ops_scope, current_agent_ops_context
from app.services.agent_confirmation import Origin
from app.services.canvas_image_refs import ReferenceGraph
from firefly_projects import ProjectNotFound, ProjectStoreError

TOOLS = frozenset(f'generate.{name}' for name in ('preflight', 'request_batch', 'batch_status', 'stop_batch'))
TERMINAL = frozenset({'done', 'stopped', 'rejected'})
_TTL = 3600
_MAX_PREFLIGHTS = 256
_MAX_ITEMS = 200
_LOCK = threading.RLock()
_PREFLIGHTS = {}
_BATCHES = {}
_GATES = WeakValueDictionary()
_COPY = re.compile(r'(?:文案\s*[:：]|(?:on[- ]image|overlay)\s+(?:copy|text)\b|(?:叠加|添加|写上)(?:文字|文案))', re.I)
_LOG = logging.getLogger(__name__)


def _api():
    from app.routers import api_agent_tools
    return api_agent_tools


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _snapshot(project):
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    try:
        return store.image_reference_snapshot(project)
    except ProjectNotFound as exc:
        raise agent_error(404, 'PROJECT_NOT_FOUND', 'Project not found') from exc
    except ProjectStoreError as exc:
        raise agent_error(409, 'PREFLIGHT_STALE', 'Cannot read the current canvas') from exc


@dataclass(frozen=True)
class Caller:
    project: str
    actor: str
    client: str = ''

    def key(self, canvas):
        nodes = {n['id']: n for n in canvas.get('nodes', []) if n.get('type') == NODE_TYPE_AGENT}
        actor = self.actor
        if not actor and self.client:
            matched = [n['id'] for n in nodes.values() if n.get('mcp_session') == self.client]
            if len(matched) == 1:
                actor = matched[0]
            elif len(nodes) == 1 and not next(iter(nodes.values())).get('mcp_session'):
                actor = next(iter(nodes))
        node = nodes.get(actor)
        if self.actor and (node is None or (self.client and node.get('mcp_session') not in (None, '', self.client))):
            raise agent_error(404, 'AGENT_NOT_FOUND', 'No matching Agent node in this project')
        binding = ((actor, node.get('created_at'), node.get('mcp_session'),
                    node.get('agentCli') or 'pi', node.get('current_branch') or '') if node else ())
        return (self.project, self.client, binding)


def external_caller(project, node, sid):
    if not re.fullmatch(r'[A-Za-z0-9._~-]{1,128}', sid or ''):
        raise agent_refs.invalid('A stable Mcp-Session-Id (1..128 URL-safe characters) is required')
    return Caller(project, node, sid)


def _selection(args, canvas):
    nodes = {n['id']: n for n in canvas.get('nodes', [])}
    if 'group_id' in args:
        group = nodes.get(args['group_id'])
        if not group or group.get('type') != NODE_TYPE_GROUP:
            raise agent_error(404, 'NODE_NOT_FOUND', 'Generation group not found')
        # Group decoration, plan prompt/reference nodes and prior output cards are not tasks.
        ids = [key for key in group.get('items', []) if key in nodes
               and nodes[key].get('type') in (NODE_TYPE_IMAGE, 'image', 'smart-container')
               and 'excel_ref_index' not in nodes[key]
               and (not nodes[key].get('images') or any(nodes[key].get(k) for k in
                    ('runSettings', 'runPrompt', 'prompt', 'promptDraftText', 'inputNodeIds')))]
    else:
        ids = args['node_ids']
    if not isinstance(ids, list) or not 1 <= len(ids) <= _MAX_ITEMS:
        raise agent_refs.invalid(f'Expected 1..{_MAX_ITEMS} generation nodes')
    for node in ids:
        agent_refs.identifier(node, 'node_id')
    if len(set(ids)) != len(ids):
        raise agent_refs.invalid('Duplicate generation nodes')
    return tuple(ids), nodes


def _providers():
    return _api()._app_main.load_api_providers(read_only=True)


def _thumb(project, asset):
    return f'/api/project-library/{quote(project, safe="")}/media/{quote(asset, safe="")}'


def _product_presets(canvas):
    if not any(node.get('promptPresetId') for node in canvas.get('nodes', [])):
        return frozenset()
    from app.routers.api_prompt_libraries import PROMPT_LIBRARY_PATH
    try:
        with open(PROMPT_LIBRARY_PATH, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return frozenset()
    categories = {}
    libraries = data.get('libraries') if isinstance(data, dict) else None
    for library in libraries if isinstance(libraries, list) else ():
        items = library.get('items') if isinstance(library, dict) else None
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('category'), str):
                categories.setdefault(item['id'], set()).add(item['category'])
    return frozenset(ident for ident, values in categories.items() if ident and values == {'product'})


def _product_task(node, incoming, canvas, presets):
    # Excel plan lineage is positive evidence; role declarations survive disabling a ref.
    def classified(item):
        return item.get('category') == 'product' or item.get('promptPresetId') in presets or (
            bool(item.get('excel_task_id')) and 'excel_ref_index' not in item and
            any(isinstance(item.get(k), dict) for k in ('planLineage', 'excel_task_data')))
    return (classified(node) or any(classified(src) for src in incoming if src.get('type') == NODE_TYPE_PROMPT)
            or any(edge.get('role') == 'product' for edge in agent_refs.managed_edges(canvas, node['id'])))


@dataclass(frozen=True)
class Item:
    node: str
    plan: object
    stamp: str


def _resolve(project, node, canvas, catalog, providers, graph, presets):
    source = graph.nodes.get(node)
    if source is None:
        raise agent_error(404, 'NODE_NOT_FOUND', f'Node not found: {node}')
    incoming = graph._inputs(source)
    # Resolve product evidence from the same numbered image positions the task will submit.
    selection = graph.resolve(node, track_saved_mentions=True)
    product_missing = _product_task(source, incoming, canvas, presets) and not any(
        role == 'product' and asset for _, asset, role in graph.image_positions(selection))
    plan = _api().resolve_generation(project, node, canvas, catalog, saved=canvas, providers=providers,
                                    preflight=True, graph=graph, selection=selection)
    source_inputs = {k: v for k, v in source.items() if k not in {'lastFailure', 'agentStatus'}}
    stamp = _digest((source_inputs, [upstream['id'] for upstream in incoming], selection,
                     plan.payload.model_dump(exclude={'base_revision'}), plan.final_prompt,
                     plan.route.to_dict(), plan.reference_files, product_missing))
    return Item(node, plan, stamp), product_missing


@dataclass
class Preview:
    caller: Caller
    key: tuple
    loop: object
    selector: dict
    items: tuple
    providers: str
    blockers: tuple
    deadline: float
    prepared: dict = field(default_factory=dict)
    batch: object = None


@dataclass
class Batch:
    id: str
    preview: Preview
    loop: object
    models: tuple
    max_retries: int
    origin: Origin
    turn_id: str | None
    confirm_id: str = field(default_factory=lambda: 'confirm-' + uuid4().hex[:12])
    confirmations: int = 0
    status: str = 'awaiting_confirmation'
    reason: str | None = None
    concurrency: int = 1
    approved_count: int = 0
    spent: int = 0
    rows: list = field(default_factory=list)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def status_dict(self):
        return {'batch_id': self.id, 'status': self.status, 'reason': self.reason,
                'items': [dict(row) for row in self.rows], 'concurrency': self.concurrency,
                'approved_count': self.approved_count, 'spent': self.spent}

    def signal(self):
        self.changed.set()
        self.changed = asyncio.Event()

    def stop(self):
        self.cancelled.set()
        if self.status not in TERMINAL:
            self.status = 'stopped'
            self.reason = 'stopped'
            for row in self.rows:
                if row['status'] == 'queued':
                    row['status'] = 'stopped'
            self.signal()


def _evict(now):
    for pid, preview in list(_PREFLIGHTS.items()):
        batch = preview.batch
        if preview.deadline <= now and (not batch or _settled(batch)):
            del _PREFLIGHTS[pid]
            if batch:
                _BATCHES.pop(batch.id, None)


def _settled(batch):
    return (batch.status in TERMINAL or batch.status == 'paused') and not any(
        row['status'] == 'running' for row in batch.rows)


def is_running(project, batch_id):
    with _LOCK:
        batch = _BATCHES.get(batch_id)
        return bool(batch and batch.preview.caller.project == project and not _settled(batch))


def shutdown(loop):
    with _LOCK:
        for pid, preview in list(_PREFLIGHTS.items()):
            if preview.loop is loop:
                if preview.batch:
                    preview.batch.stop()
                    _BATCHES.pop(preview.batch.id, None)
                del _PREFLIGHTS[pid]


def invalidate(project, actor=''):
    """Drop handles on actual session destruction, not at normal turn end."""
    with _LOCK:
        for pid, preview in list(_PREFLIGHTS.items()):
            binding = preview.key[2]
            if preview.key[0] == project and (not actor or binding and binding[0] == actor):
                if preview.batch:
                    batch = preview.batch
                    _BATCHES.pop(batch.id, None)
                    if not batch.loop.is_closed():
                        batch.loop.call_soon_threadsafe(_stop_locked, batch)
                del _PREFLIGHTS[pid]


def _stop_locked(batch):
    with _LOCK:
        batch.stop()


def _read_preview(caller, args, loop):
    project = caller.project
    canvas, catalog = _snapshot(project)
    key = caller.key(canvas)
    ids, nodes = _selection(args, canvas)
    graph, presets = ReferenceGraph(canvas, project, catalog), _product_presets(canvas)
    items, blockers, warnings, output = [], [], [], []
    used = []
    try:
        providers = _providers()
    except (OSError, ValueError):
        providers = []
    for node in ids:
        try:
            item, missing = _resolve(project, node, canvas, catalog, providers, graph, presets)
        except HTTPException as exc:
            code = exc.detail.get('code', 'NO_ROUTE') if isinstance(exc.detail, dict) else 'NO_ROUTE'
            blockers.append({'node_id': node, 'code': code})
            continue
        plan = item.plan
        items.append(item)
        legacy_thumbs = {image.asset_id: image.url for image in plan.legacy_images}
        refs = [{'n': n, 'role': role or 'general', 'thumb': legacy_thumbs.get(asset, _thumb(project, asset))}
                for n, (asset, role) in enumerate(
                     zip(plan.payload.reference_asset_ids, plan.reference_roles), 1)]
        output.append({'node_id': node, 'final_prompt': plan.final_prompt, 'user_prompt': plan.user_prompt,
                       'refs': refs, 'model': plan.route.model, 'provider': plan.route.provider_id,
                       'aspect_ratio': plan.request.aspect_ratio, 'size': plan.payload.size, 'count': plan.request.count})
        if plan.route.model not in used:
            used.append(plan.route.model)
        if missing:
            blockers.append({'node_id': node, 'code': 'MISSING_PRODUCT_REF'})
        if len(refs) > 8:
            warnings.append({'node_id': node, 'code': 'REFS_MANY'})
        if _COPY.search(plan.final_prompt) or any(line.strip() and line.strip() in plan.final_prompt for line in
                                                str(nodes[node].get('overlay_plan') or '').splitlines()):
            warnings.append({'node_id': node, 'code': 'COPY_IN_PROMPT'})
    prepared = {'items': output, 'blockers': blockers, 'warnings': warnings,
                'total_images': sum(row['count'] for row in output), 'models_used': used}
    total = prepared['total_images']
    if total > _api().GENERATION_SESSION_LIMIT:
        blockers.append({'node_id': None, 'code': 'BUDGET_EXCEEDED'})
    # A blocked request needs no retained execution snapshot.
    return (Preview(caller, key, loop, args, () if blockers else tuple(items), _digest(providers),
                    tuple(blockers), time.monotonic() + _TTL, prepared), prepared)


def _fresh(preview, item=None):
    project = preview.caller.project
    try:
        canvas, catalog = _snapshot(project)
        providers = _providers()
        if preview.caller.key(canvas) != preview.key or _digest(providers) != preview.providers:
            raise ValueError
        if item is None and _selection(preview.selector, canvas)[0] != tuple(x.node for x in preview.items):
            raise ValueError
        if item is not None and 'group_id' in preview.selector and item.node not in _selection(preview.selector, canvas)[0]:
            raise ValueError
        graph, presets = ReferenceGraph(canvas, project, catalog), _product_presets(canvas)
        for old in (preview.items if item is None else (item,)):
            current, missing = _resolve(project, old.node, canvas, catalog, providers, graph, presets)
            if missing or current.stamp != old.stamp:
                raise ValueError
        return providers
    except (HTTPException, OSError, ValueError) as exc:
        raise agent_error(409, 'PREFLIGHT_STALE', 'Generation inputs or provider configuration changed; run preflight again') from exc


def _options(args):
    models = args.get('fallback_models', [])
    max_retries = args.get('max_retries', 2)
    if (not isinstance(models, list) or len(models) > 20 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 200 or '\n' in item or '\r' in item for item in models
    ) or type(max_retries) is not int or max_retries < 0):
        raise agent_refs.invalid('Invalid fallback_models or max_retries')
    return tuple(models), max_retries


async def call(tool, caller, args):
    api = _api()
    from app.services.agent_host import agent_host_manager as host
    context = current_agent_ops_context()
    originating_turn = context.turn_id if context else host.get_current_turn_id(caller.project, caller.actor)
    originating_session = host.conversation_session(caller.project, caller.actor)
    captured_origin = Origin.capture(originating_session, originating_turn) if originating_session else None
    if tool == 'generate.preflight':
        agent_refs.require_object(args, tool, optional=('node_ids', 'group_id'))
        if len(args) != 1:
            raise agent_refs.invalid('Supply exactly one of node_ids or group_id')
        if 'group_id' in args:
            agent_refs.identifier(args['group_id'], 'group_id')
        preview, result = await asyncio.to_thread(_read_preview, caller, args, asyncio.get_running_loop())
        with _LOCK:
            _evict(time.monotonic())
            if len(_PREFLIGHTS) >= _MAX_PREFLIGHTS:
                raise agent_error(429, 'PREFLIGHT_LIMIT', 'Too many retained preflights; try later')
            pid = uuid4().hex
            _PREFLIGHTS[pid] = preview
        return {'preflight_id': pid, **result}

    if tool == 'generate.request_batch':
        agent_refs.require_object(args, tool, required=('preflight_id',), optional=('fallback_models', 'max_retries'))
        ident = agent_refs.identifier(args['preflight_id'], 'preflight_id')
        models, retries = _options(args)
    else:
        agent_refs.require_object(args, tool, required=('batch_id',),
                                  optional=('wait_seconds',) if tool == 'generate.batch_status' else ())
        ident = agent_refs.identifier(args['batch_id'], 'batch_id')
        wait = args.get('wait_seconds', 0)
        if type(wait) not in (int, float) or not 0 <= wait <= 600:
            raise agent_refs.invalid('wait_seconds must be 0..600')

    canvas, _ = await asyncio.to_thread(_snapshot, caller.project)
    key = caller.key(canvas)
    now = time.monotonic()
    with _LOCK:
        _evict(now)
        if tool == 'generate.request_batch':
            preview = _PREFLIGHTS.get(ident)
            if not preview or preview.key != key or preview.loop is not asyncio.get_running_loop():
                raise agent_error(404, 'PREFLIGHT_NOT_FOUND', 'Preflight not found in this session')
            if preview.batch:
                return _receipt(preview.batch)
            if preview.blockers:
                raise agent_error(409, 'PREFLIGHT_BLOCKED', 'Preflight has blockers', details={'blockers': list(preview.blockers)})
        else:
            batch = _BATCHES.get(ident)
            if not batch or batch.preview.key != key or batch.loop is not asyncio.get_running_loop():
                raise agent_error(404, 'BATCH_NOT_FOUND', 'Batch not found in this session')
            if tool == 'generate.stop_batch':
                batch.stop()
                return batch.status_dict()
            if not wait or _settled(batch):
                return batch.status_dict()
            changed = batch.changed
    if tool == 'generate.batch_status':
        try:
            await asyncio.wait_for(changed.wait(), wait)
        except TimeoutError:
            pass
        canvas, _ = await asyncio.to_thread(_snapshot, caller.project)
        with _LOCK:
            if _BATCHES.get(ident) is not batch or caller.key(canvas) != key:
                raise agent_error(404, 'BATCH_NOT_FOUND', 'Batch session ended')
            return batch.status_dict()

    if not key[2]:
        raise agent_error(409, 'CONFIRM_MISSING_NODE_ID', 'Select an existing Agent node for browser confirmation',
                          '在画布中添加或选择 Agent 节点，并在 MCP URL 中设置 ?node=<节点 ID>。')
    try:
        await asyncio.to_thread(_fresh, preview)
    except HTTPException:
        with _LOCK:
            if _PREFLIGHTS.get(ident) is preview and preview.batch:
                return _receipt(preview.batch)
        raise
    loop = asyncio.get_running_loop()
    session = await host.get_or_create_session(caller.project, key[2][0], recover_history=False)
    node = next(n for n in canvas['nodes'] if n['id'] == key[2][0])
    if node.get('agentCli') and node['agentCli'] != session.agent_cli and not session.is_busy:
        from app.routers.api_agent_nodes import _apply_node_host
        _apply_node_host(caller.project, key[2][0], session)
    session.autonomy = node.get('autonomy') or session.autonomy or 'standard'
    turn = originating_turn
    origin = captured_origin or Origin.capture(session, turn)
    origin.turn = turn
    if caller.client:
        origin.hosted = False
    with _LOCK:
        if _PREFLIGHTS.get(ident) is not preview:
            raise agent_error(409, 'PREFLIGHT_STALE', 'Preflight session ended')
        if preview.batch:
            return _receipt(preview.batch)
        preview.deadline = time.monotonic() + _TTL
        batch = Batch(uuid4().hex, preview, loop, models, retries, origin, turn,
                      rows=[{'node_id': item.node, 'status': 'queued', 'attempts': 0, 'model': item.plan.route.model,
                             'result_node_id': None, 'task_id': None, 'failure': None} for item in preview.items])
        preview.batch = _BATCHES[batch.id] = batch
        # Retain only the originating turn, not the request's ContextVars or external lease.
        api._track_lifetime_task(contextvars.Context().run(asyncio.create_task, _run(batch)))
        return _receipt(batch)


def _receipt(batch):
    automatic = batch.origin.session.autonomy == 'autonomous'
    return {'batch_id': batch.id,
            'status': 'approved' if automatic or batch.approved_count else 'awaiting_confirmation',
            **({'confirm_id': batch.confirm_id} if not automatic else {})}


def _fail(batch, reason):
    if batch.status not in TERMINAL:
        batch.status, batch.reason = 'rejected', reason
        for row in batch.rows:
            if row['status'] == 'queued':
                row['status'] = 'rejected'
        batch.signal()


async def _confirm(batch, project, actor, options, count, item=None):
    api = _api()
    gate_key = (batch.loop, project, actor)
    with _LOCK:
        gate = _GATES.setdefault(gate_key, asyncio.Lock())
    async with gate:
        if batch.cancelled.is_set() or not batch.origin.valid():
            return {'approved': False, 'reason': 'stopped'}
        prepared = deepcopy(batch.preview.prepared)
        if item is not None:
            prepared['items'] = [row for row in prepared['items'] if row['node_id'] == item.node]
            prepared['total_images'] = count
        confirm_id = batch.confirm_id if not batch.confirmations else 'confirm-' + uuid4().hex[:12]
        batch.confirmations += 1
        return await api.request_confirmation_core(
            project, actor, 'generate.request_batch', {**options, 'total_images': count, 'preflight': prepared},
            f'批准生成 {count} 张图片（消耗外部额度）\n'
            f'备选模型：{json.dumps(options["fallback_models"], ensure_ascii=False)}\n'
            f'允许自动重试次数：{min(batch.max_retries, 2)}',
            None, transient_session=batch.origin.session, cancel_event=batch.cancelled, confirm_id=confirm_id)


async def _run(batch):
    api = _api()
    preview = batch.preview
    project, actor = preview.key[0], preview.key[2][0]
    store = api._app_main.FIREFLY_PROJECT_STORE
    count = sum(item.plan.request.count for item in preview.items)
    options = {'batch_id': batch.id, 'fallback_models': list(batch.models), 'max_retries': batch.max_retries}
    try:
        cautious = batch.origin.session.autonomy == 'cautious'
        first = preview.items[0] if cautious else None
        admitted_count = first.plan.request.count if first else count
        decision = await _confirm(batch, project, actor, options, admitted_count, first)
        if not decision.get('approved'):
            with _LOCK:
                _fail(batch, decision.get('reason'))
            return

        def approve():
            # Check the exact inputs and debit the existing shared session counter once.
            with store._lock:
                providers = deepcopy(_fresh(preview))
                if not batch.origin.valid():
                    raise agent_error(409, 'CONFIRM_STALE', 'Owning Agent turn changed',
                                      '重新预检并请求批准，不复用旧批准。', retryable=False)
                with _LOCK:
                    if batch.status != 'awaiting_confirmation' or _BATCHES.get(batch.id) is not batch:
                        return None
                    api.enforce_generation_limit(project, actor, admitted_count)
                    batch.approved_count = admitted_count
                return providers

        providers = await run_blocking_to_completion(approve)
        if providers is None or batch.cancelled.is_set():
            return
        with _LOCK:
            batch.status = 'running'
            batch.signal()
        with agent_ops_scope(project_id=project, agent_node_id=actor, turn_id=batch.turn_id,
                             tool='generate.request_batch', args=options) as op:
            journal_batch = {'root': op.op_id, 'root_exists': False, 'ids': set(), 'tokens': {op.op_id},
                             'tasks': [], 'unregistered': {}}
            token = api._BATCH_GENERATION.set(journal_batch)
            try:
                for item, row in zip(preview.items, batch.rows):
                    if batch.cancelled.is_set() or batch.status == 'paused':
                        break
                    if cautious and item is not first:
                        decision = await _confirm(batch, project, actor, options, item.plan.request.count, item)
                        if not decision.get('approved'):
                            with _LOCK:
                                _fail(batch, decision.get('reason'))
                            break
                        with store._lock:
                            _fresh(preview, item)
                            if batch.cancelled.is_set() or not batch.origin.valid():
                                break
                            api.enforce_generation_limit(project, actor, item.plan.request.count)
                            batch.approved_count += item.plan.request.count
                    await _run_item(batch, row, item, actor, providers)
            finally:
                api.release_batch_journal(store, project, op.op_id, journal_batch, journal_batch['ids'])
                api._BATCH_GENERATION.reset(token)
        with _LOCK:
            if not batch.cancelled.is_set() and batch.status not in TERMINAL and batch.status != 'paused':
                batch.status = 'done'
            batch.signal()
    except asyncio.CancelledError:
        _stop_locked(batch)
        raise
    except HTTPException as exc:
        with _LOCK:
            _fail(batch, exc.detail.get('code') if isinstance(exc.detail, dict) else 'BATCH_FAILED')
    except Exception:
        _LOG.exception('Agent generation batch %s failed', batch.id)
        with _LOCK:
            _fail(batch, 'BATCH_FAILED')
    finally:
        if not batch.cancelled.is_set():
            await batch.origin.continue_with(batch.status_dict())


async def _run_item(batch, row, item, actor, providers):
    api = _api()
    project = batch.preview.caller.project

    from app.services import agent_generation_retry as retry
    from firefly_generation import ModelRoutingError

    store = api._app_main.FIREFLY_PROJECT_STORE
    plan, parent = item.plan, None
    tried = {plan.route.model}
    ref_retried, aspect_retried = False, False

    for attempt in range(min(batch.max_retries, 2) + 1):
        submitted = None

        def admit(task_id, result_id):
            nonlocal submitted
            if parent:
                canvas = store.open_project(project)['canvas']
                retry.guard_withdrawal(canvas, parent[1], item.plan.payload.reference_asset_ids)
                retry.check_tip(store, project, *parent)
            _fresh(batch.preview, item)
            with _LOCK:
                if (batch.status != 'running' or _BATCHES.get(batch.id) is not batch
                        or not batch.origin.valid()):
                    raise agent_error(409, 'BATCH_STOPPED', 'Batch stopped before this item started')
                submitted = task_id
                row.update(status='running', attempts=attempt + 1, task_id=task_id,
                           result_node_id=result_id, model=plan.route.model)
                if attempt == 0:
                    batch.spent += item.plan.request.count
                batch.signal()

        failure = {'code': 'GENERATION_FAILED', 'message': '生成未能启动', 'hint': '请检查任务状态。'}
        try:
            await api.trigger_generation_core(project, item.node, actor, plan=plan, validate_start=admit,
                                              approved_provider=deepcopy(plan.provider),
                                              retry_caller=batch.preview.caller, retry_parent=parent,
                                              retry_models=batch.models, retry_batch_id=batch.id)
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                failure = exc.detail
        except Exception:
            _LOG.exception('Agent batch item %s admission did not return normally', item.node)

        # A lost receipt is not a failed submission. Wait for durable settlement.
        status = None
        while submitted:
            try:
                current = await api.get_task_status(project, submitted)
            except HTTPException as exc:
                if exc.status_code == 404:
                    break
                raise
            if current['status'] in ('succeeded', 'failed', 'timeout', 'cancelled'):
                status = current
                break
            await asyncio.sleep(0.1)
        with _LOCK:
            if not status:
                row.update(status='stopped' if batch.cancelled.is_set() else 'failed', failure=failure)
                batch.signal()
                return
            failure = status.get('failure')
            row.update(status=status['status'], failure=failure)
            batch.signal()
            code = (failure or {}).get('code')
            if batch.cancelled.is_set():
                return
            if code == 'QUOTA_EXHAUSTED':
                batch.status, batch.reason = 'paused', code
                row.update(status='paused', failure={**failure, 'hint':
                           f'已暂停剩余 {batch.approved_count - batch.spent} 张；请充值或更换服务商。'})
                batch.signal()
                return
        record = (status.get('result') or {}).get('history_record') or {}
        if (attempt >= min(batch.max_retries, 2) or status['status'] != 'failed'
                or not record.get('automatic_retry') or not record.get('retry_root_id')):
            return
        parent = (record['retry_root_id'], submitted, record['retry_index'])
        try:
            canvas = await asyncio.to_thread(store.canvas, project)
            retry.guard_withdrawal(canvas, submitted, item.plan.payload.reference_asset_ids)
        except HTTPException as exc:
            with _LOCK:
                row.update(failure=exc.detail, last_failure=failure)
                batch.signal()
            return
        if batch.cancelled.is_set():
            return
        delay = 0
        if code == 'MODEL_UNAVAILABLE':
            for model in batch.models:
                if model in tried:
                    continue
                tried.add(model)
                try:
                    plan = retry.model_plan(plan, providers, model)
                    break
                except (ModelRoutingError, HTTPException):
                    continue
            else:
                return
        elif code == 'TRANSIENT':
            delay = (5, 20)[attempt]
        elif code == 'RATE_LIMITED':
            delay = record['retry_after_seconds']
        elif code == 'REF_UPLOAD_FAILED' and not ref_retried:
            ref_retried = True
        elif code == 'INVALID_PARAMS' and record.get('adjust_aspect') and not aspect_retried:
            aspect_retried = True
            try:
                aligned = retry.aligned_plan(plan, providers)
            except (ModelRoutingError, HTTPException, ValueError):
                return
            if aligned is None:
                return
            plan = aligned
        else:
            return
        with _LOCK:
            row.update(status='running', last_failure=failure)
            batch.signal()
        if delay:
            try:
                await asyncio.wait_for(batch.cancelled.wait(), timeout=delay)
            except TimeoutError:
                pass
        if batch.cancelled.is_set():
            with _LOCK:
                row['status'] = 'stopped'
                batch.signal()
            return
