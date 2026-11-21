"""Live approval ownership and detached tool delivery, scoped to one logical turn."""
import asyncio
import contextvars
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import inspect
import json
import logging
from uuid import uuid4

from app.services.agent_errors import agent_error, translate_detail
from app.services.agent_ops_context import current_agent_ops_context

_current = contextvars.ContextVar('agent_confirmation', default=None)
_transport_hosted = contextvars.ContextVar('agent_confirmation_hosted', default=True)


@contextmanager
def confirmation_transport(*, hosted):
    token = _transport_hosted.set(hosted)
    try:
        yield
    finally:
        _transport_hosted.reset(token)


_LOG = logging.getLogger(__name__)


def _api():
    from app.routers import api_agent_tools
    return api_agent_tools


@dataclass
class Origin:
    session: object
    host: str
    generation: int
    epoch: str
    turn: str | None
    user_turn: str | None
    hosted: bool

    @classmethod
    def capture(cls, session, turn=None):
        return cls(session, session.agent_cli, session.generation, session.input_epoch,
                   turn or session._current_turn_id,
                   session._last_user_turn_id or session._current_turn_id,
                   bool((session._current_turn_id or session._last_user_turn_id)
                        and (session.is_running or session._reader_task is not None)))

    def valid(self):
        from app.services.agent_host import agent_host_manager as host
        s = self.session
        return (host.conversation_session(s.project_id, s.node_id) is s and not s._stopping
                and (s.agent_cli, s.generation, s.input_epoch) == (self.host, self.generation, self.epoch)
                and (s._last_user_turn_id or s._current_turn_id) in (None, self.user_turn))

    async def continue_with(self, result):
        if self.hosted and self.turn and self.valid():
            from app.services.agent_host import agent_host_manager as host
            await host.send_system_continuation(self.session,
                '系统确认操作已结束，请根据结果继续，不要重复执行：' + json.dumps(result, ensure_ascii=False, default=str),
                turn_id=self.turn, host=self.host, generation=self.generation, epoch=self.epoch)


class WaitingOperation:
    def __init__(self, origin):
        self.origin = origin
        self.pending = asyncio.get_running_loop().create_future()
        self.snapshot = None
        self.admitted = False
        self.providers = None

    def check(self, project, canvas, *, admit=False):
        if self.snapshot is None:
            return
        if (not self.origin.valid() or project != self.origin.session.project_id
                or not any(n.get('id') == self.origin.session.node_id and n.get('type') == 'smart-agent'
                           for n in canvas.get('nodes', []))
                or (not self.admitted and canvas != self.snapshot)
                or (self.providers is not None and self.providers != _api()._app_main.load_api_providers(read_only=True))):
            raise agent_error(409, 'CONFIRM_STALE', 'Confirmation inputs or owning session changed',
                              '重新读取画布和预检，不复用旧批准。', retryable=False)
        if admit:
            self.admitted = True


def validate_pending(project, canvas, *, admit=False):
    state = _current.get()
    if state:
        state.check(project, canvas, admit=admit)


@contextmanager
def generation_settlement():
    """Already admitted image work must settle even after its approval origin expires."""
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


def originating_turn():
    state = _current.get()
    return state.origin.turn if state and state.origin else None


def resumable(function):
    """Let the HTTP/MCP receipt end while the original, single execution waits."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        if _current.get() is not None:
            return await function(*args, **kwargs)
        from app.services.agent_host import agent_host_manager as host
        values = signature.bind(*args, **kwargs).arguments
        context = current_agent_ops_context()
        caller = values.get('caller') or values.get('retry_caller')
        project = values.get('project_id') or getattr(caller, 'project', None)
        request = values.get('request')
        actor = (request.query_params.get('agent_node_id', '') if request else
                 values.get('agent_node_id') or getattr(caller, 'actor', None))
        session = host.conversation_session(project, actor) if project and actor else None
        origin = Origin.capture(session, context.turn_id if context else None) if session else None
        if origin and caller and getattr(caller, 'client', ''):
            origin.hosted = False
        state = WaitingOperation(origin)

        async def execute():
            token = _current.set(state)
            try:
                return await function(*args, **kwargs)
            finally:
                _current.reset(token)

        task = _api()._track_lifetime_task(asyncio.create_task(execute()))
        try:
            completed, _ = await asyncio.wait((task, state.pending), return_when=asyncio.FIRST_COMPLETED)
            if task in completed:
                return task.result()
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

        async def finish():
            try:
                result = await task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                result = {'error': translate_detail(getattr(exc, 'status_code', 500),
                                                    getattr(exc, 'detail', str(exc)))}
            if state.origin:
                try:
                    await state.origin.continue_with(result)
                except Exception:
                    _LOG.exception('Approved operation completed but its system continuation could not be delivered')

        _api()._track_lifetime_task(contextvars.Context().run(asyncio.create_task, finish()))
        return state.pending.result()

    return wrapped


def _owned_entity(project, actor, tool, params, canvas, turn):
    """Only committed server journal creation deltas can grant the exception."""
    from app.services.agent_ops_store import list_operations, list_turn_snapshot
    from app.services.agent_ops_delta import _id
    from firefly_opsdb import OpsDb
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    db = OpsDb(store.root)
    if tool == 'canvas.delete_node':
        if not turn:
            return False
        entity = next((n for n in canvas.get('nodes', []) if n['id'] == params.get('node_id')), None)
        if not entity or entity.get('userTouchedAt') is not None:
            return False
        kind, ident = 'node', entity['id']
        records = list_turn_snapshot(db, project, turn)
    elif tool in ('canvas.disconnect', 'canvas.delete_edge'):
        source, target = params.get('from_node', params.get('from')), params.get('to_node', params.get('to'))
        entity = next((e for e in canvas.get('connections', []) if (e.get('from'), e.get('to')) == (source, target)), None)
        if not entity:
            return False
        kind, ident = 'connection', _id(entity, 'connection')
        records = list_operations(db, project, limit=1000)
    else:
        return False
    for record in records:
        if record['undone'] or record['agent_node_id'] != actor:
            continue
        for change in record['after']:
            if (change['entity'], change['id'], change['path']) == (kind, ident, []):
                previous = next((old for old in record['before'] if
                                 (old['entity'], old['id'], old['path']) == (kind, ident, [])), None)
                if previous and not previous['before']['present'] and change['after'].get('value') == entity:
                    return True
    return False


async def request(project, actor, tool, params, message, timeout, *, allow_automatic=True,
                  transient_session=None, cancel_event=None, confirm_id=None):
    from app.services.agent_host import agent_host_manager as host, SAFETY_FLOOR_TOOLS
    store = _api()._app_main.FIREFLY_PROJECT_STORE
    canvas = store.canvas(project)
    node = next((n for n in canvas.get('nodes', []) if n.get('id') == actor and n.get('type') == 'smart-agent'), None)
    if node is None:
        raise agent_error(404, 'NODE_NOT_FOUND', 'Confirmation Agent node does not exist',
                          '先选择当前画布上已有的 Agent 节点。', details={'node_id': actor})
    session = transient_session or await host.get_or_create_session(project, actor)
    if node.get('agentCli') and node['agentCli'] != session.agent_cli and not session.is_busy:
        from app.routers.api_agent_nodes import _apply_node_host
        _apply_node_host(project, actor, session)
    autonomy = node.get('autonomy') or session.autonomy or 'standard'
    session.autonomy = autonomy
    state = _current.get()
    origin = state.origin if state and state.origin else Origin.capture(session)
    if not _transport_hosted.get():
        origin.hosted = False
    if state:
        state.origin = origin
    owned = _owned_entity(project, actor, tool, params, canvas, session._current_turn_id) if tool in (
        'canvas.delete_node', 'canvas.delete_edge', 'canvas.disconnect') else False
    if owned or (allow_automatic and autonomy == 'autonomous' and tool not in SAFETY_FLOOR_TOOLS):
        return {'approved': True}
    if state:
        state.snapshot = deepcopy(canvas)
        if tool in ('generate.trigger', 'generate.retry', 'canvas.edit_erase', 'canvas.batch_edit'):
            state.providers = deepcopy(_api()._app_main.load_api_providers(read_only=True))
    confirm_id = confirm_id or 'confirm-' + uuid4().hex[:12]
    decision = asyncio.get_running_loop().create_future()
    metadata = {'host': origin.host, 'generation': origin.generation, 'turn_id': origin.turn}

    async def publish(event):
        # Resolving a registered old approval must remove its replay card after a host switch too.
        host._persist_event(session, event, host=origin.host, generation=origin.generation,
                            recovery=event['type'] == 'confirm_resolved')
        for handler in list(session.event_handlers):
            try:
                await handler(session, event)
            except Exception:
                pass

    async def response(cid, approved):
        if cid == confirm_id and type(approved) is bool and not decision.done():
            valid = origin.valid() and not (cancel_event and cancel_event.is_set())
            decision.set_result({'approved': approved and valid,
                                 'reason': ('rejected' if not approved else None) if valid else 'session_ended'})

    host.on_confirm(session, response)

    async def wait():
        try:
            await publish({'type': 'confirm', 'confirm_id': confirm_id, 'tool': tool,
                           'params': deepcopy(params), 'message': message, **metadata})
            while not decision.done():
                if not origin.valid() or (cancel_event and cancel_event.is_set()):
                    decision.set_result({'approved': False, 'reason': 'stopped' if cancel_event and cancel_event.is_set() else 'session_ended'})
                    break
                await asyncio.wait((decision,), timeout=.1)
            result = decision.result()
            await publish({'type': 'confirm_resolved', 'confirm_id': confirm_id, 'tool': tool, **result, **metadata})
            return result
        except asyncio.CancelledError:
            await publish({'type': 'confirm_resolved', 'confirm_id': confirm_id, 'tool': tool,
                           'approved': False, 'reason': 'stopped', **metadata})
            raise
        finally:
            if response in session.confirm_handlers:
                session.confirm_handlers.remove(response)

    waiter = _api()._track_lifetime_task(asyncio.create_task(wait()))
    paid = tool in ('generate.trigger', 'generate.retry', 'canvas.edit_erase', 'canvas.batch_edit')
    duration = 0 if state and paid else timeout
    if duration is None:
        return await waiter
    try:
        completed, _ = await asyncio.wait((waiter,), timeout=max(0, duration))
    except asyncio.CancelledError:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        raise
    if completed:
        return waiter.result()
    pending = {'approved': False, 'reason': 'pending', 'code': 'CONFIRM_PENDING',
               'status': 'awaiting_confirmation', 'confirm_id': confirm_id}
    if state:
        if not state.pending.done():
            state.pending.set_result(pending)
        return await waiter
    return pending
