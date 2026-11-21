"""Bound, finite presentation receipts; presentation never grants execution authority (#406)."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
from time import monotonic
from uuid import uuid4
from weakref import WeakKeyDictionary, ref

from fastapi import HTTPException

from app.constants import NODE_TYPE_AGENT
from app.services.agent_errors import agent_error
from app.services.agent_node_host_settings import read_host_fields
from app.services.agent_refs import invalid

MAX_PAYLOAD = 65_536
MAX_RECORDS = 256
TTL = 1800
_RECORDS = WeakKeyDictionary()


def _object(required, **properties):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


_ID = {'type': 'string', 'minLength': 1, 'maxLength': 128}
_TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
_PROPERTIES = {
    'action': {'type': 'string', 'enum': ['show', 'get']},
    'kind': {'type': 'string', 'enum': ['plan', 'question', 'diff']},
    'title': {**_TEXT, 'maxLength': 200},
    'steps': {'type': 'array', 'minItems': 1, 'maxItems': 50,
              'items': _object(['id', 'text'], id=_ID, text=_TEXT)},
    'questions': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': _object(
        ['id', 'text', 'options', 'recommended'], id=_ID, text=_TEXT, recommended=_ID,
        options={'type': 'array', 'minItems': 2, 'maxItems': 10,
                 'items': _object(['id', 'text'], id=_ID, text=_TEXT)})},
    'op_id': _ID,
    'diff_id': _ID,
    'presentation_id': _ID,
}
SCHEMA = _object(['action'], **_PROPERTIES)
SCHEMA['oneOf'] = [
    _object(['action', 'presentation_id'], action={'type': 'string', 'enum': ['get']}, presentation_id=_ID),
    *[_object(['action', 'kind', 'title', data], action={'type': 'string', 'enum': ['show']},
              kind={'type': 'string', 'enum': [kind]}, title=_PROPERTIES['title'], **{data: _PROPERTIES[data]})
      for kind, data in [('plan', 'steps'), ('question', 'questions'), ('diff', 'diff_id'), ('diff', 'op_id')]],
]
TOOL = {'name': 'agent.present', 'description': 'show 呈现计划提案、多题选择或真实 diff_id/op_id 差异；get 读取原收据与答案。问题推荐项必须是实际选项，外部 MCP 须沿用原 Mcp-Session-Id。不批准、不执行、不创建节点，不承诺重启续跑。', 'inputSchema': SCHEMA}


def _agent_present_valid(value, schema):
    if 'oneOf' in schema and sum(_agent_present_valid(value, branch) for branch in schema['oneOf']) != 1:
        return False
    kind = schema['type']
    if kind == 'object':
        props = schema['properties']
        return (isinstance(value, dict) and set(schema['required']) <= value.keys() <= props.keys()
                and all(_agent_present_valid(v, props[k]) for k, v in value.items()))
    if kind == 'string':
        return (isinstance(value, str) and bool(value.strip()) and len(value) <= schema.get('maxLength', 128)
                and ('enum' not in schema or value in schema['enum']))
    if kind == 'array':
        return (isinstance(value, list) and schema['minItems'] <= len(value) <= schema['maxItems']
                and all(_agent_present_valid(v, schema['items']) for v in value))
    return False


def _bounded(value):
    try:
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8')) <= MAX_PAYLOAD
    except UnicodeError:
        return False


def agent_present_validate(args):
    if not _agent_present_valid(args, SCHEMA) or not _bounded(args):
        raise invalid('Invalid or oversized agent.present arguments')
    for key in ('steps', 'questions'):
        if key in args and len({item['id'] for item in args[key]}) != len(args[key]):
            raise invalid(f'{key} IDs must be unique')
    for question in args.get('questions', []):
        options = {option['id'] for option in question['options']}
        if len(options) != len(question['options']) or question['recommended'] not in options:
            raise invalid('Options must be unique and recommendation must name an option')


def agent_present_stale():
    return agent_error(409, 'CONFLICT', 'Presentation is missing, expired or belongs to another binding',
                       retryable=False, details={'reason': 'presentation_stale'})


def _agent_present_node(store, project, actor):
    node = next((n for n in store.canvas(project)['nodes'] if n.get('id') == actor), None)
    if not node or node.get('type') not in (NODE_TYPE_AGENT, 'agent'):
        raise agent_error(404, 'AGENT_NOT_FOUND', 'An existing Agent node is required')
    return node


def _agent_present_host(node):
    return read_host_fields(node)['agentCli']


@dataclass
class Record:
    project: str
    actor: str
    owner: tuple
    session: object
    host: str
    generation: int
    turn: str | None
    expires: float
    presentation: dict
    epoch: str
    node_identity: tuple
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    receipt: dict | None = None
    delivery_error: tuple | None = None


def _records(store):
    records = _RECORDS.setdefault(store, OrderedDict())
    now = monotonic()
    for key in [key for key, record in records.items() if record.expires <= now]:
        del records[key]
    return records


async def _publish(manager, record):
    session = record.session()
    event = {'type': record.presentation['kind'], 'presentation': deepcopy(record.presentation), 'turn_id': record.turn}
    if record.delivery_error:
        event['delivery_error'] = deepcopy(record.delivery_error[1])
    if not session or not manager._persist_event(session, event, host=record.host, generation=record.generation):
        raise agent_error(503, 'SERVER_NOT_READY', 'Presentation could not be persisted')
    await manager._broadcast_event(session, event, host=record.host, generation=record.generation)


def _agent_present_identity(node):
    return node.get('created_at'), _agent_present_host(node), node.get('mcp_session'), node.get('current_branch')


def _agent_present_fresh(store, manager, record):
    session = record.session()
    try:
        node = _agent_present_node(store, record.project, record.actor)
    except Exception:
        return False
    return bool(record.expires > monotonic() and session
                and manager.conversation_session(record.project, record.actor) is session
                and _agent_present_identity(node) == record.node_identity
                and session.agent_cli == record.host and session.generation == record.generation
                and session.input_epoch == record.epoch and not session._stopping
                and (record.owner[0] == 'mcp' or (
                    session.status not in ('stopped', 'crashed', 'aborted')
                    and (session.is_running or (record.host == 'codex' and session.status == 'running'
                                               and not session._turn_open))
                    and session._last_user_turn_id == record.turn)))


def _expire(store, manager, record):
    if record.presentation['state'] == 'pending' and not _agent_present_fresh(store, manager, record):
        record.presentation['state'] = 'expired'


def question(store, manager, project, actor, presentation_id):
    record = _records(store).get(presentation_id)
    if not record or (record.project, record.actor, record.presentation['kind']) != (project, actor, 'question'):
        raise agent_present_stale()
    _expire(store, manager, record)
    return record


def question_status(store, manager, project, actor, presentation_id):
    record = question(store, manager, project, actor, presentation_id)
    result = {'presentation': deepcopy(record.presentation), 'answerable': record.presentation['state'] == 'pending'}
    if record.delivery_error:
        result['delivery_error'] = deepcopy(record.delivery_error[1])
    return result


async def answer(store, manager, project, actor, presentation_id, body, send):
    if (not isinstance(body, dict) or not {'answers'} <= body.keys() <= {'answers', 'request_id'}
            or not isinstance(body['answers'], dict)):
        raise invalid('Expected answers and optional request_id')
    if 'request_id' in body and (not isinstance(body['request_id'], str) or not body['request_id'].strip()
                               or len(body['request_id']) > 128 or not body['request_id'].isprintable()):
        raise invalid('request_id must be a nonblank printable string of at most 128 characters')
    record = question(store, manager, project, actor, presentation_id)
    async with record.lock:
        questions = record.presentation['data']['questions']
        answers = body['answers']
        if (set(answers) != {q['id'] for q in questions}
                or any(not isinstance(answers[q['id']], str)
                       or answers[q['id']] not in {o['id'] for o in q['options']} for q in questions)):
            raise invalid('Answers must specify exactly one valid option for every question')
        if record.receipt:
            if answers != record.presentation.get('answers'):
                raise agent_error(409, 'CONFLICT', 'Question already has a different answer',
                                  retryable=False, details={'reason': 'answer_conflict'})
            if record.delivery_error:
                raise HTTPException(record.delivery_error[0], deepcopy(record.delivery_error[1]))
            return deepcopy(record.receipt)

        request_id = body.get('request_id', 'presentation-' + presentation_id)

        def guard():
            _expire(store, manager, record)
            if record.presentation['state'] == 'expired' or not _agent_present_fresh(store, manager, record):
                raise agent_present_stale()
            if record.owner[0] == 'hosted' and request_id in record.session()._follow_up_ids:
                raise agent_error(409, 'CONFLICT', 'request_id already identifies another input', retryable=False)

        guard()
        lines = [record.presentation['title']]
        for q in questions:
            option = next(o for o in q['options'] if o['id'] == answers[q['id']])
            lines.append(f"{q['text']}: {option['text']}")
        previous = deepcopy(record.presentation)
        record.presentation.update(state='answered', answers=deepcopy(answers))
        record.receipt = {'presentation': deepcopy(record.presentation), 'input': None}
        try:
            # Store the human decision in its original runtime before a new Codex
            # user turn advances generation. This is not a delivery/approval event.
            await _publish(manager, record)
        except BaseException:
            record.presentation, record.receipt = previous, None
            raise
        if record.owner[0] == 'hosted':
            try:
                record.receipt['input'] = await send({'message': '\n'.join(lines),
                    'request_id': request_id}, guard)
            except HTTPException as exc:
                # An uncertain pipe write is not safe to retry with another answer POST.
                record.delivery_error = (exc.status_code, deepcopy(exc.detail))
                try:
                    await _publish(manager, record)
                except Exception as publish_error:
                    # Receipt storage must not replace the actual host delivery failure.
                    raise exc from publish_error
                raise
        return deepcopy(record.receipt)


def _resolve_diff(store, project, actor, owner, args):
    if 'op_id' in args:
        from app.services.agent_ops_store import get_operation
        from firefly_opsdb import OpsDb
        db = OpsDb(store.root)
        operation = get_operation(db, project, args['op_id'])
        if not operation or operation.get('agent_node_id') != actor:
            raise agent_present_stale()
        with db.connect(project, create_project_dir=False, create_db=False) as conn:
            turn = conn.execute('SELECT owner, kind FROM agent_ops_turns WHERE project_id=? AND turn_id=?',
                                (project, operation['turn_id'])).fetchone()
        expected = (actor, 'hosted') if owner[0] == 'hosted' else (owner[1], 'external')
        if turn is None or tuple(turn) != expected:
            raise agent_present_stale()
        return {'source': 'operation', **{key: deepcopy(operation[key]) for key in
                ('op_id', 'turn_id', 'tool', 'before', 'after', 'undone')}}
    from app.services import agent_batch_prompt
    binding = ('agent', actor) if owner[0] == 'hosted' else ('mcp', owner[1], actor)
    # The existing preview has only a consuming read; presentation must not take it
    # away from its original confirmation/CAS path.
    with agent_batch_prompt._LOCK:
        preview = agent_batch_prompt._PENDING.get(store, {}).get(args['diff_id'])
        if not preview or preview[0] <= monotonic() or preview[1:3] != (project, binding):
            raise agent_batch_prompt.stale()
        items = preview[3]
    agent_batch_prompt.build_mutations(store.canvas(project), items, 0)
    return {'source': 'preview', 'diff_id': args['diff_id'],
            'items': [item.public() for item in items], 'applied': False}


async def agent_present_call(store, manager, project, actor, args, *, owner=('hosted',), external_turn=None):
    agent_present_validate(args)
    node = _agent_present_node(store, project, actor)
    session = manager.conversation_session(project, actor)
    external = owner[0] == 'mcp'
    if external:
        from app.services.agent_batch import external_caller
        external_caller(project, actor, owner[1]).key(store.canvas(project))
        if session is None and args['action'] == 'show':
            session = await manager.get_or_create_session(project, actor, recover_history=False)
            if not session.is_running and session._current_turn_id is None:
                session.agent_cli = _agent_present_host(node)
    if not session or session.agent_cli != _agent_present_host(node) or session._stopping:
        raise agent_present_stale()
    records = _records(store)
    if args['action'] == 'get':
        record = records.get(args['presentation_id'])
        if not record or (record.project, record.actor, record.owner) != (project, actor, owner):
            raise agent_present_stale()
        if not external and (record.session() is not session or record.host != session.agent_cli
                             or record.generation != session.generation or record.epoch != session.input_epoch):
            raise agent_present_stale()
        _expire(store, manager, record)
        return {'presentation': deepcopy(record.presentation)}
    turn = external_turn if external else manager.get_current_turn_id(project, actor)
    if not external and (not turn or not session.is_running):
        raise agent_present_stale()
    if args['kind'] == 'diff':
        data = _resolve_diff(store, project, actor, owner, args)
    else:
        data_key = 'questions' if args['kind'] == 'question' else 'steps'
        data = {data_key: deepcopy(args[data_key])}
    if not _bounded(data):
        raise invalid('Resolved presentation exceeds the payload limit')
    p = {'schema': 1, 'id': uuid4().hex, 'kind': args['kind'], 'title': args['title'],
         'state': 'pending' if args['kind'] == 'question' else 'presented', 'data': data}
    record = Record(project, actor, owner, ref(session), session.agent_cli, session.generation, turn,
                    monotonic() + TTL, p, session.input_epoch, _agent_present_identity(node))
    while len(records) >= MAX_RECORDS:
        records.popitem(last=False)
    records[p['id']] = record
    await _publish(manager, record)
    return {'presentation': deepcopy(p)}
