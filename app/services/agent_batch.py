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
from app.services import agent_refs, agent_image_settings
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
    id: str = field(default_factory=lambda: 'item-' + uuid4().hex)


def _resolve(project, node, canvas, catalog, providers, graph, presets, override=None):
    source = graph.nodes.get(node)
    if source is None:
        raise agent_error(404, 'NODE_NOT_FOUND', f'Node not found: {node}')
    incoming = graph._inputs(source)
    # Resolve product evidence from the same numbered image positions the task will submit.
    selection = graph.resolve(node, track_saved_mentions=True,
                              batch_body_override=(override or {}).get('prompt'))
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
    store: object = None
    original: object = None
    overrides: dict = field(default_factory=dict)
    source_proof: str = ''


@dataclass
class Batch:
    id: str
    preview: Preview
    loop: object
    models: tuple
    max_retries: int
    origin: Origin
    turn_id: str | None
    confirm_id: str = field(default_factory=lambda: 'batch-confirm-' + uuid4().hex)
    confirmations: int = 0
    selection_decided: bool = False
    claimed_confirm_id: str | None = None
    gate_ready: asyncio.Event = field(default_factory=asyncio.Event)
    gate_error: object = None
    pending_event: object = None
    status: str = 'awaiting_confirmation'
    reason: str | None = None
    concurrency: int = 1
    approved_count: int = 0
    approved_item_ids: tuple = ()
    approval_sha256: str | None = None
    spent: int = 0
    rows: list = field(default_factory=list)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def status_dict(self):
        return {'batch_id': self.id, 'status': self.status, 'reason': self.reason,
                'items': [dict(row) for row in self.rows], 'concurrency': self.concurrency,
                'preview_revision': self.preview.prepared['preview_revision'],
                'snapshot_sha256': self.preview.prepared['snapshot_sha256'],
                'approved_count': self.approved_count, 'spent': self.spent,
                'approved_item_ids': list(self.approved_item_ids), 'approval_sha256': self.approval_sha256}

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


def invalidate(project, actor='', *, strict=False):
    """Drop handles on actual session destruction, not at normal turn end."""
    error = None
    with _LOCK:
        for pid, preview in list(_PREFLIGHTS.items()):
            binding = preview.key[2]
            if preview.key[0] == project and (not actor or binding and binding[0] == actor):
                if preview.batch:
                    batch = preview.batch
                    _BATCHES.pop(batch.id, None)
                    if strict:
                        try:
                            _stop_locked(batch, audit=True)
                        except HTTPException as exc:
                            error = error or exc
                    elif not batch.loop.is_closed():
                        batch.loop.call_soon_threadsafe(_stop_locked, batch)
                del _PREFLIGHTS[pid]
    if error is not None:
        raise error


def _stop_locked(batch, audit=False):
    with _LOCK:
        try:
            if audit:
                handler = next((h for h in batch.origin.session.confirm_handlers
                                if getattr(h, 'batch_confirm_id', None) == batch.confirm_id and h.batch_pending()), None)
                if handler is not None:
                    handler.batch_stop()
                elif batch.status not in TERMINAL:
                    from app.services.agent_host import agent_host_manager as host
                    event = {'type': 'batch_stopped', 'batch_id': batch.id, 'reason': 'stopped',
                             'project_id': batch.preview.caller.project, 'agent_node_id': batch.preview.key[2][0],
                             'source': 'mcp' if batch.preview.caller.client else 'rest', 'turn_id': batch.turn_id,
                             'preview_revision': batch.preview.prepared['preview_revision'],
                             'snapshot_sha256': batch.preview.prepared['snapshot_sha256'],
                             'approved_item_ids': list(batch.approved_item_ids), 'approval_sha256': batch.approval_sha256,
                             'approved_count': batch.approved_count, 'spent': batch.spent}
                    if not host._persist_event(batch.origin.session, event, host=batch.origin.host,
                                               generation=batch.origin.generation, recovery=True):
                        raise agent_error(500, 'JOURNAL_WRITE_FAILED', 'Batch stop audit could not be saved', retryable=False)

                    async def deliver():
                        for listener in list(batch.origin.session.event_handlers):
                            try:
                                await listener(batch.origin.session, event)
                            except Exception:
                                pass

                    _api()._track_lifetime_task(asyncio.create_task(deliver()))
        finally:
            # Audit failure must never leave a stopped caller's capability usable.
            batch.stop()


def _read_preview(caller, args, loop, *, original=None, overrides=None, survivors=None, revision=1):
    project = caller.project
    canvas, catalog = _snapshot(project)
    source_proof = _digest((canvas, catalog))
    key = caller.key(canvas)
    ids, nodes = _selection(args, canvas)
    overrides = deepcopy(overrides or {})
    if survivors is not None:
        ids = tuple(item.node for item in survivors)
    for node in canvas.get('nodes', []):
        patch = overrides.get(node['id'], {}).get('runSettings')
        if patch is not None:
            node['runSettings'], _ = agent_image_settings.merge_patch(node.get('runSettings') or {}, patch)
    graph, presets = ReferenceGraph(canvas, project, catalog), _product_presets(canvas)
    items, blockers, warnings, output = [], [], [], []
    used = []
    try:
        providers = _providers()
    except (OSError, ValueError):
        providers = []
    for node in ids:
        try:
            item, missing = _resolve(project, node, canvas, catalog, providers, graph, presets, overrides.get(node))
            if survivors is not None:
                item = Item(item.node, item.plan, item.stamp, next(old.id for old in survivors if old.node == node))
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
        output.append({'item_id': item.id, 'node_id': node, 'final_prompt': plan.final_prompt, 'user_prompt': plan.user_prompt,
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
    prepared = {'items': output, 'blockers': blockers, 'warnings': warnings, 'preview_revision': revision,
                'total_images': sum(row['count'] for row in output), 'models_used': used}
    total = prepared['total_images']
    if total > _api().GENERATION_SESSION_LIMIT:
        warnings.append({'node_id': None, 'code': 'BUDGET_EXCEEDED'})
    # A blocked request needs no retained execution snapshot.
    preview = Preview(caller, key, loop, args, () if blockers else tuple(items), _digest(providers),
                      tuple(blockers), time.monotonic() + _TTL, prepared,
                      store=_api()._app_main.FIREFLY_PROJECT_STORE, original=original, overrides=overrides,
                      source_proof=source_proof)
    prepared['snapshot_sha256'] = _snapshot_digest(preview)
    return preview, prepared


def _snapshot_digest(preview, models=(), max_retries=2):
    source = preview.original or preview
    return _digest({'revision': preview.prepared['preview_revision'], 'scope': preview.key,
                    'selector': source.selector, 'providers': preview.providers,
                    'source_proof': source.source_proof, 'source_stamps': [item.stamp for item in source.items],
                    'items': preview.prepared['items'], 'stamps': [item.stamp for item in preview.items],
                    'options': {'fallback_models': list(models), 'max_retries': max_retries}})


def _fresh(preview, item=None, *, capture=False):
    if preview.original is not None:
        if item is not None:
            item = next(old for old in preview.original.items if old.node == item.node)
        preview = preview.original
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
        return (providers, _digest((canvas, catalog))) if capture else providers
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
    originating_turn = context.turn_id if context else None
    # Receipt-only MCP tools don't reserve a turn, but hosted calls retain their server-owned turn.
    if not originating_turn and not caller.client:
        originating_turn = host.get_current_turn_id(caller.project, caller.actor)
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
    existing = None
    with _LOCK:
        _evict(now)
        if tool == 'generate.request_batch':
            preview = _PREFLIGHTS.get(ident)
            if not preview or preview.key != key or preview.loop is not asyncio.get_running_loop():
                raise agent_error(404, 'PREFLIGHT_NOT_FOUND', 'Preflight not found in this session')
            existing = preview.batch
            if preview.blockers:
                raise agent_error(409, 'PREFLIGHT_BLOCKED', 'Preflight has blockers', details={'blockers': list(preview.blockers)})
        else:
            batch = _BATCHES.get(ident)
            if not batch or batch.preview.key != key or batch.loop is not asyncio.get_running_loop():
                raise agent_error(404, 'BATCH_NOT_FOUND', 'Batch not found in this session')
            if tool == 'generate.stop_batch':
                _stop_locked(batch, audit=True)
                return batch.status_dict()
            if not wait or _settled(batch):
                return batch.status_dict()
            changed = batch.changed
    if existing is not None:
        return await _ready_receipt(existing)
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
            existing = preview.batch if _PREFLIGHTS.get(ident) is preview else None
        if existing is not None:
            return await _ready_receipt(existing)
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
        batch = preview.batch
        if batch is None:
            preview.deadline = time.monotonic() + _TTL
            batch = Batch(uuid4().hex, preview, loop, models, retries, origin, turn,
                          rows=[{'item_id': item.id, 'node_id': item.node, 'status': 'queued', 'attempts': 0, 'model': item.plan.route.model,
                                 'result_node_id': None, 'task_id': None, 'failure': None} for item in preview.items])
            preview.prepared['snapshot_sha256'] = _snapshot_digest(preview, models, retries)
            preview.batch = _BATCHES[batch.id] = batch
            # Retain only the originating turn, not the request's ContextVars or external lease.
            api._track_lifetime_task(contextvars.Context().run(asyncio.create_task, _run(batch)))
    return await _ready_receipt(batch)


async def _ready_receipt(batch):
    await batch.gate_ready.wait()
    if batch.gate_error is not None:
        raise batch.gate_error
    return _receipt(batch)


def _receipt(batch):
    return {'batch_id': batch.id,
            'status': 'approved' if batch.approved_count else 'awaiting_confirmation',
            'confirm_id': batch.confirm_id}


def _fail(batch, reason):
    if batch.status not in TERMINAL:
        batch.status, batch.reason = 'rejected', reason
        for row in batch.rows:
            if row['status'] == 'queued':
                row['status'] = 'rejected'
        batch.signal()


def _check_actor_binding(preview):
    try:
        canvas, _ = _snapshot(preview.caller.project)
        if preview.caller.key(canvas) != preview.key:
            raise ValueError
    except (HTTPException, ValueError) as exc:
        raise agent_error(409, 'CONFIRM_STALE', 'Batch actor binding changed') from exc


def human_preview(project, actor, batch_id):
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        with _LOCK:
            _evict(time.monotonic())
            batch = _BATCHES.get(batch_id)
            if (not batch or batch.preview.store is not store or batch.preview.key[0] != project
                    or batch.preview.key[2][0] != actor or batch.loop is not asyncio.get_running_loop()
                    or not batch.origin.valid()):
                raise agent_error(409, 'CONFIRM_STALE', 'Batch does not belong to this active human scope')
            _check_actor_binding(batch.preview)
            handler = next((handler for handler in batch.origin.session.confirm_handlers
                            if getattr(handler, 'batch_confirm_id', None) == batch.confirm_id
                            and handler.batch_pending()), None)
            return {'batch_id': batch.id, 'status': batch.status, 'preflight': deepcopy(batch.preview.prepared),
                    'confirm_id': batch.confirm_id if handler else None, 'actionable': bool(handler),
                    'decision_token': handler.batch_decision_token if handler else None}


def _mutation_body(body, action):
    required = ('preview_revision', 'snapshot_sha256', 'item_id') + (('patch',) if action == 'edit' else ())
    agent_refs.require_object(body, 'preview.' + action, required=required)
    if type(body['preview_revision']) is not int or body['preview_revision'] < 1:
        raise agent_refs.invalid('preview_revision must be a positive integer')
    if not isinstance(body['snapshot_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', body['snapshot_sha256']):
        raise agent_refs.invalid('snapshot_sha256 must be a SHA256 hex digest')
    agent_refs.identifier(body['item_id'], 'item_id')
    if action == 'remove':
        return {}
    patch = body['patch']
    if not isinstance(patch, dict) or not patch or set(patch) - {'prompt', 'runSettings'}:
        raise agent_refs.invalid('patch accepts only prompt and runSettings')
    if 'prompt' in patch and (not isinstance(patch['prompt'], str) or not patch['prompt'].strip()
                              or len(patch['prompt']) > 100000):
        raise agent_refs.invalid('prompt must be nonempty text of at most 100000 characters')
    if 'runSettings' in patch:
        agent_image_settings.validate_patch(patch['runSettings'])
    return patch


async def human_mutate(project, actor, batch_id, body, action):
    patch = _mutation_body(body, action)
    current = human_preview(project, actor, batch_id)
    with _LOCK:
        batch = _BATCHES[batch_id]
        old = batch.preview
        if (batch.status != 'awaiting_confirmation' or batch.selection_decided or not current['actionable']
                or body['preview_revision'] != old.prepared['preview_revision']
                or body['snapshot_sha256'] != old.prepared['snapshot_sha256']):
            raise agent_error(409, 'CONFIRM_STALE', 'Preview is no longer mutable at this revision')
        item = next((value for value in old.items if value.id == body['item_id']), None)
        if item is None:
            raise agent_error(409, 'CONFIRM_STALE', 'Item is not in this preview')
        survivors = tuple(value for value in old.items if value is not item) if action == 'remove' else old.items
        overrides = deepcopy(old.overrides)
        override = overrides.setdefault(item.node, {})
        if 'prompt' in patch:
            override['prompt'] = patch['prompt']
        if 'runSettings' in patch:
            override['runSettings'] = {**override.get('runSettings', {}), **patch['runSettings']}
    await asyncio.to_thread(_fresh, old)
    candidate, _ = await asyncio.to_thread(
        _read_preview, old.caller, old.selector, old.loop, original=old.original or old, overrides=overrides,
        survivors=survivors, revision=old.prepared['preview_revision'] + 1)
    if candidate.blockers:
        raise agent_error(409, 'PREFLIGHT_BLOCKED', 'Edited preview has blockers',
                          details={'blockers': list(candidate.blockers)})
    _, source_proof = await asyncio.to_thread(_fresh, old, capture=True)
    if candidate.source_proof != source_proof or candidate.providers != old.providers:
        raise agent_error(409, 'PREFLIGHT_STALE', 'Candidate was prepared from different inputs or providers')
    if _digest(await asyncio.to_thread(_providers)) != old.providers:
        raise agent_error(409, 'PREFLIGHT_STALE', 'Providers changed during preflight')
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        with _LOCK:
            if (batch.preview is not old or _BATCHES.get(batch_id) is not batch or batch.preview.store is not store
                    or batch.status != 'awaiting_confirmation' or batch.selection_decided
                    or batch.cancelled.is_set() or not batch.origin.valid()):
                raise agent_error(409, 'CONFIRM_STALE', 'Preview changed during preflight')
            # Re-preflight does IO without either install lock; this final Store snapshot closes the source CAS window.
            if _digest(_snapshot(project)) != source_proof:
                raise agent_error(409, 'PREFLIGHT_STALE', 'Source changed after preflight')
            handler = next((h for h in batch.origin.session.confirm_handlers
                            if getattr(h, 'batch_confirm_id', None) == current['confirm_id'] and h.batch_pending()), None)
            if handler is None:
                raise agent_error(409, 'CONFIRM_STALE', 'Original gate ended during preflight')
            if action == 'edit' and all(
                (left.plan.payload.model_dump(exclude={'base_revision'}), left.plan.route.to_dict(),
                 left.plan.user_prompt, left.plan.final_prompt, left.plan.reference_files) ==
                (right.plan.payload.model_dump(exclude={'base_revision'}), right.plan.route.to_dict(),
                 right.plan.user_prompt, right.plan.final_prompt, right.plan.reference_files)
                for left, right in zip(old.items, candidate.items)
            ):
                return human_preview(project, actor, batch_id)
            candidate.prepared['snapshot_sha256'] = _snapshot_digest(candidate, batch.models, batch.max_retries)
            candidate.deadline = old.deadline
            new_id = 'batch-confirm-' + uuid4().hex if candidate.items else None
            options = {'batch_id': batch.id, 'fallback_models': list(batch.models), 'max_retries': batch.max_retries}
            revision = {'batch_id': batch.id, 'action': action, 'item_id': item.id,
                        'old_preview_revision': old.prepared['preview_revision'],
                        'old_snapshot_sha256': old.prepared['snapshot_sha256'],
                        'preview_revision': candidate.prepared['preview_revision'],
                        'snapshot_sha256': candidate.prepared['snapshot_sha256']}
            params = _gate_params(batch, options, candidate.prepared)
            saved_old, saved_new = handler.batch_prepare_replace(
                new_id, params, _gate_message(batch, candidate.prepared['total_images']), revision)
            if (not batch.origin.valid() or batch.cancelled.is_set() or not handler.batch_pending()
                    or batch.preview is not old or _digest(_snapshot(project)) != source_proof):
                raise agent_error(409, 'CONFIRM_STALE', 'Preview origin or inputs ended during audit')
            candidate.batch = batch
            batch.preview = candidate
            batch.rows = [{**row, 'model': value.plan.route.model} for value in candidate.items
                          for row in batch.rows if row['item_id'] == value.id]
            batch.confirm_id = new_id
            batch.pending_event = saved_new
            batch.gate_ready = asyncio.Event()
            handler.batch_replace(saved_old)
            if not candidate.items:
                batch.stop()
                batch.reason = 'empty_batch'
                return human_preview(project, actor, batch_id)
            batch.signal()
            ready = batch.gate_ready
    await ready.wait()
    return human_preview(project, actor, batch_id)


async def human_confirm(project, actor, body):
    from app.services.agent_host import agent_host_manager as host
    body = _validate_decision(body)
    if not body.get('confirm_id'):
        raise agent_refs.invalid('confirm_id is required')
    session = host.conversation_session(project, actor)
    for handler in list(session.confirm_handlers) if session else ():
        if getattr(handler, 'batch_confirm_id', None) == body['confirm_id']:
            await handler(body['confirm_id'], body)
            return {'ok': True, 'confirm_id': body['confirm_id'], 'approved': body['approved']}
    raise agent_error(409, 'CONFIRM_STALE', 'Batch confirmation is no longer pending')


async def websocket_confirm(project, actor, command):
    from app.services.agent_host import agent_host_manager as host
    token = command.get('decision_token')
    body = {key: value for key, value in command.items() if key not in ('type', 'decision_token')}
    body = _validate_decision(body)
    session = host.conversation_session(project, actor)
    handler = next((handler for handler in session.confirm_handlers
                    if getattr(handler, 'batch_confirm_id', None) == body.get('confirm_id')),
                   None) if session else None
    if (handler is None or not isinstance(token, str) or token != handler.batch_decision_token
            or not handler.batch_pending()):
        raise agent_error(409, 'CONFIRM_STALE', 'A current HTTP-bound batch decision_token is required')
    return await human_confirm(project, actor, body)


def _validate_decision(value):
    value = {'approved': value} if type(value) is bool else value
    if not isinstance(value, dict) or set(value) - {'confirm_id', 'approved', 'approved_item_ids', 'snapshot_sha256'}:
        raise agent_refs.invalid('Unknown batch confirmation fields')
    if type(value.get('approved')) is not bool:
        raise agent_refs.invalid('approved must be a boolean')
    if 'confirm_id' in value and (not isinstance(value['confirm_id'], str) or not value['confirm_id']):
        raise agent_refs.invalid('confirm_id must be a nonempty string')
    if 'snapshot_sha256' in value and (not isinstance(value['snapshot_sha256'], str)
                                       or not re.fullmatch(r'[a-f0-9]{64}', value['snapshot_sha256'])):
        raise agent_refs.invalid('snapshot_sha256 must be a SHA256 hex digest')
    if 'approved_item_ids' in value and 'snapshot_sha256' not in value:
        raise agent_refs.invalid('Subset decisions require snapshot_sha256')
    if not value['approved'] and 'approved_item_ids' in value:
        raise agent_refs.invalid('A rejection must not include approved_item_ids')
    return value


async def _decision(batch, value, items, confirm_id, audit):
    value = _validate_decision(value)
    preview = batch.preview
    _check_actor_binding(preview)
    source_proof = None
    if value['approved']:
        _, source_proof = await asyncio.to_thread(_fresh, preview, capture=True)
        if _digest(await asyncio.to_thread(_providers)) != preview.providers:
            raise agent_error(409, 'PREFLIGHT_STALE', 'Providers changed during decision validation')
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    with store._lock:
        with _LOCK:
            if (batch.preview.store is not store or not batch.origin.valid() or batch.cancelled.is_set()
                    or _BATCHES.get(batch.id) is not batch
                    or batch.preview is not preview or batch.claimed_confirm_id == confirm_id
                    or confirm_id != batch.confirm_id
                    or value.get('confirm_id', confirm_id) != confirm_id):
                raise agent_error(409, 'CONFIRM_STALE', 'Batch origin or Store ended')
            _check_actor_binding(batch.preview)
            snapshot = batch.preview.prepared['snapshot_sha256']
            if 'snapshot_sha256' in value and value['snapshot_sha256'] != snapshot:
                raise agent_error(409, 'CONFIRM_STALE', 'Batch snapshot changed')
            if not value['approved']:
                result = {'approved': False, 'reason': 'rejected'}
                audit(result)
                if not batch.origin.valid() or batch.cancelled.is_set():
                    raise agent_error(409, 'CONFIRM_STALE', 'Batch origin ended during audit')
                batch.selection_decided = True
                batch.claimed_confirm_id = confirm_id
                return result
            if _digest(_snapshot(preview.caller.project)) != source_proof:
                raise agent_error(409, 'PREFLIGHT_STALE', 'Source changed during decision validation')
            selected = value.get('approved_item_ids', [item.id for item in items])
            if not isinstance(selected, list) or any(not isinstance(ident, str) for ident in selected):
                raise agent_refs.invalid('approved_item_ids must be a list of strings')
            available = {item.id for item in items}
            if not selected or len(set(selected)) != len(selected) or not set(selected) <= available:
                raise agent_error(409, 'CONFIRM_STALE', 'Selected items are not a nonempty subset of this gate')
            canonical = [item.id for item in items if item.id in selected]
            approval = hashlib.sha256((snapshot + json.dumps(canonical, ensure_ascii=False,
                                                            separators=(',', ':'))).encode()).hexdigest()
            result = {'approved': True, 'approved_item_ids': canonical,
                      'snapshot_sha256': snapshot, 'approval_sha256': approval}
            audit(result)
            if (not batch.origin.valid() or batch.cancelled.is_set() or batch.preview is not preview
                    or _digest(_snapshot(preview.caller.project)) != source_proof):
                raise agent_error(409, 'CONFIRM_STALE', 'Batch inputs or origin ended during audit')
            batch.selection_decided = True
            batch.claimed_confirm_id = confirm_id
            return result


def _gate_message(batch, count):
    return (f'批准生成 {count} 张图片（消耗外部额度）\n'
            f'备选模型：{json.dumps(list(batch.models), ensure_ascii=False)}\n'
            f'允许自动重试次数：{min(batch.max_retries, 2)}')


def _gate_params(batch, options, prepared, item=None):
    stage = 'pay_item' if item is not None else (
        'select' if batch.origin.session.autonomy == 'cautious' else 'pay_batch')
    return {**options, 'total_images': prepared['total_images'], 'preflight': prepared, 'approval_stage': stage}


async def _confirm(batch, project, actor, options, count, item=None):
    api = _api()
    # Independent batches must return their audited pending receipts without waiting for another human gate.
    gate_key = (batch.loop, project, actor, batch.id)
    with _LOCK:
        gate = _GATES.setdefault(gate_key, asyncio.Lock())
    async with gate:
        if batch.cancelled.is_set() or not batch.origin.valid():
            return {'approved': False, 'reason': 'stopped'}
        if item is not None:
            batch.confirm_id = 'batch-confirm-' + uuid4().hex
        while not batch.cancelled.is_set():
            prepared = deepcopy(batch.preview.prepared)
            items = (item,) if item else batch.preview.items
            count = sum(value.plan.request.count for value in items)
            if item is not None:
                prepared['items'] = [row for row in prepared['items'] if row['node_id'] == item.node]
                prepared['total_images'] = count
            confirm_id = batch.confirm_id
            batch.confirmations += 1
            def handler(value):
                return _decision(batch, value, items, confirm_id, handler.audit)
            handler.gate_ready = batch.gate_ready
            handler.strict_journal = True
            handler.origin = batch.origin
            handler.waiting_guard = lambda: _check_actor_binding(batch.preview)
            handler.audit_metadata = {'project_id': project, 'agent_node_id': actor, 'batch_id': batch.id,
                                      'source': 'mcp' if batch.preview.caller.client else 'rest',
                                      'preview_revision': prepared['preview_revision'],
                                      'snapshot_sha256': prepared['snapshot_sha256']}
            handler.initial_event = batch.pending_event
            batch.pending_event = None
            result = await api.request_confirmation_core(
                project, actor, 'generate.request_batch', _gate_params(batch, options, prepared, item),
                _gate_message(batch, count),
                None, allow_automatic=False, transient_session=batch.origin.session,
                cancel_event=batch.cancelled, confirm_id=confirm_id, decision_handler=handler)
            if result.get('reason') != 'replaced':
                return result
        return {'approved': False, 'reason': 'stopped'}


async def _run(batch):
    api = _api()
    preview = batch.preview
    project, actor = preview.key[0], preview.key[2][0]
    store = api._app_main.FIREFLY_PROJECT_STORE
    count = sum(item.plan.request.count for item in preview.items)
    options = {'batch_id': batch.id, 'fallback_models': list(batch.models), 'max_retries': batch.max_retries}
    try:
        cautious = batch.origin.session.autonomy == 'cautious'
        decision = await _confirm(batch, project, actor, options, count)
        if not decision.get('approved'):
            with _LOCK:
                _fail(batch, decision.get('reason'))
            return

        preview = batch.preview
        chosen = tuple(item for item in preview.items if item.id in decision['approved_item_ids'])
        admitted_count = sum(item.plan.request.count for item in chosen)

        def approve():
            with store._lock:
                providers = deepcopy(_fresh(preview))
                if not batch.origin.valid():
                    raise agent_error(409, 'CONFIRM_STALE', 'Owning Agent turn changed',
                                      '重新预检并请求批准，不复用旧批准。', retryable=False)
                with _LOCK:
                    if batch.status != 'awaiting_confirmation' or _BATCHES.get(batch.id) is not batch:
                        return None
                    # Cautious selection reserves no paid work; each item keeps its own spending gate.
                    if not cautious:
                        api.enforce_generation_limit(project, actor, admitted_count)
                        batch.approved_count = admitted_count
                    batch.approved_item_ids = tuple(item.id for item in chosen)
                    batch.approval_sha256 = decision['approval_sha256']
                    for row in batch.rows:
                        if row['item_id'] not in batch.approved_item_ids:
                            row['status'] = 'skipped'
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
                    if item not in chosen:
                        continue
                    if batch.cancelled.is_set() or batch.status == 'paused':
                        break
                    if cautious:
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
            if not batch.gate_ready.is_set():
                batch.gate_error = exc
                _BATCHES.pop(batch.id, None)
                preview.batch = None
            _fail(batch, exc.detail.get('code') if isinstance(exc.detail, dict) else 'BATCH_FAILED')
    except Exception:
        _LOG.exception('Agent generation batch %s failed', batch.id)
        with _LOCK:
            _fail(batch, 'BATCH_FAILED')
    finally:
        batch.gate_ready.set()
        if not batch.cancelled.is_set() and batch.gate_error is None:
            await batch.origin.continue_with(batch.status_dict())


async def _run_item(batch, row, item, actor, providers):
    from .image_transport import _image_upload_retry_scope
    with _image_upload_retry_scope():
        return await _run_item_with_upload_budget(batch, row, item, actor, providers)


async def _run_item_with_upload_budget(batch, row, item, actor, providers):
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
            if current['status'] in ('succeeded', 'failed', 'timeout', 'cancelled', 'download_failed'):
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
            if status['status'] == 'download_failed':
                # An accepted paid result needs retrieval, never another generation attempt (#493).
                row.update(result=status.get('result'), retrieval=status.get('retrieval'))
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
