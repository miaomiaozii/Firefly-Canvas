"""Bounded, read-only projections of project-owned Agent event logs."""
import itertools
import json
import math
import os
from pathlib import Path

from app.constants import NODE_TYPE_AGENT
from app.services.agent_errors import agent_error
from app.services.agent_host import InvalidEventsCursor, validate_events_identifiers
from app.services.agent_node_host_settings import read_host_fields
from app.services.agent_refs import invalid, require_object
from firefly_projects import ProjectNotFound, ProjectStoreError

MAX_FILES = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_LINES = 2000
MAX_TEXT = 16000
SNIPPET = 240


def _options(tool, args, actor):
    search = tool == 'chat.search'
    require_object(args, tool, required=('query',) if search else (),
                   optional=('scope', 'node_id', 'host', 'limit', *(() if search else ('before_seq',))))
    scope = args.get('scope', 'canvas' if search else 'node')
    node = args.get('node_id', actor)
    host = args.get('host')
    limit = args.get('limit', 50)
    if scope not in ('node', 'canvas') or host not in (None, 'pi', 'codex'):
        raise invalid('scope must be node or canvas; host must be pi or codex')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise invalid('limit must be 1..200')
    if scope == 'canvas' and ('node_id' in args or 'before_seq' in args):
        raise invalid('node_id and before_seq require node scope')
    if scope == 'node' and (not isinstance(node, str) or not node.strip() or len(node) > 200):
        raise invalid('node scope requires a node_id of 1..200 characters or an initiating Agent node')
    if 'before_seq' in args and (type(args['before_seq']) is not int or args['before_seq'] < 1):
        raise invalid('before_seq must be a positive integer')
    query = args.get('query')
    if search and (not isinstance(query, str) or not query.strip() or len(query) > 200):
        raise invalid('query must contain 1..200 characters')
    return scope, node, host, limit, query.strip() if search else None


def _tail(path, budget):
    with path.open('rb') as handle:
        size = os.fstat(handle.fileno()).st_size
        length = min(size, MAX_FILE_BYTES, budget)
        handle.seek(size - length)
        data = handle.read(length)
    clipped = size > length
    if clipped:
        data = data.partition(b'\n')[2]
    # Never parse an in-flight, incomplete append as an accepted message.
    lines = data.split(b'\n')[:-1]
    return lines[-MAX_LINES:], clipped or len(lines) > MAX_LINES, length


def _plain(message):
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return ''
    content = message.get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ''.join(part['text'] for part in content
                   if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str))


def _messages(lines, project, node):
    output, pending, turns = [], {}, {}

    def flush(host):
        message = pending.pop(host, None)
        if message and message['text'].strip():
            output.append(message)

    def make(rec, host, role, text, turn):
        timestamp = rec.get('ts')
        timestamp = timestamp if type(timestamp) in (int, float) and math.isfinite(timestamp) else 0
        return {'message_id': f'{node}:{host}:{rec["seq"]}', 'node_id': node, 'host': host,
                'seq': rec['seq'], 'timestamp': timestamp, 'turn_id': turn if isinstance(turn, str) else None,
                'role': role, 'text': text[:MAX_TEXT], 'text_truncated': len(text) > MAX_TEXT}

    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (not isinstance(rec, dict) or rec.get('project_id') != project or rec.get('node_id') != node
                or type(rec.get('seq')) is not int or rec['seq'] < 1):
            continue
        event, host = rec.get('event'), rec.get('host', 'pi')
        if not isinstance(event, dict) or host not in ('pi', 'codex'):
            continue
        kind = event.get('type')
        # A real user starts a new turn; untagged legacy prompts cannot inherit an earlier ID (#457).
        turn = event.get('turn_id') if kind == 'user_prompt' else event.get('turn_id', turns.get(host))
        if kind == 'user_prompt' or (kind == 'system_continuation' and event.get('delivery') != 'queued'):
            # A queued continuation does not interrupt the assistant currently streaming.
            flush(host)
            turns[host] = turn
        if kind == 'user_prompt' or (kind == 'queued_input' and event.get('input_type') == 'steer'):
            # A steer can arrive inside an assistant stream; its final snapshot still replaces all deltas.
            text = event.get('text')
            if isinstance(text, str) and text.strip():
                output.append(make(rec, host, 'user', text, turn))
        elif kind == 'message_update':
            delta = event.get('assistantMessageEvent')
            if isinstance(delta, dict) and delta.get('type') == 'text_delta' and isinstance(delta.get('delta'), str):
                message = pending.setdefault(host, make(rec, host, 'assistant', '', turn))
                text = message['text'] + delta['delta']
                message['text'], message['text_truncated'] = text[:MAX_TEXT], message['text_truncated'] or len(text) > MAX_TEXT
                if host == 'codex':
                    # Codex maps each completed item to one text_delta, without a message_end.
                    flush(host)
        elif kind == 'message_end':
            text = _plain(event.get('message'))
            if text:
                message = pending.pop(host, None) or make(rec, host, 'assistant', '', turn)
                message.update(text=text[:MAX_TEXT], text_truncated=len(text) > MAX_TEXT)
                pending[host] = message
            flush(host)
        elif kind in ('agent_end', 'tool_execution_start'):
            flush(host)
    for host in list(pending):
        flush(host)
    return output


def _snippet(text, query=None):
    start = 0
    if query:
        offset = text.casefold().find(query.casefold())
        if offset >= 0:
            # Casefold may expand a code point; snippets must still slice the original text.
            for index, character in enumerate(text):
                offset -= len(character.casefold())
                if offset < 0:
                    start = max(0, index - 80)
                    break
    return text[start:start + SNIPPET]


def read_chat(store, project, actor, tool, args):
    scope, node, host, limit, query = _options(tool, args, actor)
    try:
        validate_events_identifiers(project, node if scope == 'node' else 'canvas')
    except InvalidEventsCursor:
        raise invalid('Invalid project or node identifier') from None
    if store is None:
        raise agent_error(503, 'SERVER_NOT_READY', 'Project store is not initialized')
    try:
        # Unlike open_project, this snapshot cannot repair documents or migrate bindings.
        canvas, _ = store.agent_inverse_snapshot(project)
    except ProjectNotFound:
        raise agent_error(404, 'PROJECT_NOT_FOUND', 'Project not found') from None
    except (ProjectStoreError, ValueError, OSError):
        raise agent_error(409, 'CHAT_HISTORY_UNAVAILABLE', 'Project history is not readable',
                          '请先在浏览器中检查项目历史；此读口不会自动修复文件。', retryable=False) from None
    nodes = {item['id']: item for item in canvas.get('nodes', [])
             if isinstance(item, dict) and isinstance(item.get('id'), str) and item.get('type') == NODE_TYPE_AGENT}
    if scope == 'node' and host is None:
        # Retired nodes have no active host; never guess which native history to show.
        if node not in nodes:
            raise invalid('host is required for a historical or missing Agent node')
        host = read_host_fields(nodes[node])['agentCli']
    directory = (Path(store.root) / project).resolve()
    history_dir = directory / 'agent-events'
    if history_dir.resolve() != history_dir or not history_dir.is_relative_to(directory):
        raise agent_error(409, 'CHAT_HISTORY_UNAVAILABLE', 'Unsafe history directory',
                          '请检查本项目历史目录；不能通过链接读取其他目录。', retryable=False)
    truncated, files = False, []
    if scope == 'node':
        files = [history_dir / f'{node}.jsonl']
    elif history_dir.is_dir():
        try:
            with os.scandir(history_dir) as entries:
                candidates = list(itertools.islice(entries, MAX_FILES + 1))
            truncated = len(candidates) > MAX_FILES
            files = sorted((Path(entry.path) for entry in candidates[:MAX_FILES]
                            if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False)), key=lambda path: path.name)
        except OSError:
            raise agent_error(409, 'CHAT_HISTORY_UNAVAILABLE', 'Project history directory is not readable',
                              '请检查本项目历史目录；此读口不会自动修复文件。', retryable=False) from None
    messages, budget = [], MAX_SCAN_BYTES
    for path in files:
        if budget <= 0:
            truncated = True
            break
        if path.is_symlink() or path.resolve().parent != history_dir or not path.is_file():
            continue
        try:
            validate_events_identifiers(project, path.stem)
            lines, clipped, used = _tail(path, budget)
            budget -= used
            truncated |= clipped
            messages.extend(_messages(lines, project, path.stem))
        except (OSError, InvalidEventsCursor):
            truncated = True
    messages = [message for message in messages if host is None or message['host'] == host]
    messages.sort(key=lambda message: (message['timestamp'], message['node_id'], message['host'], message['seq']), reverse=True)
    base = {'project_id': project, 'scope': scope, 'node_id': node if scope == 'node' else None, 'host': host}
    if query is not None:
        matches = [message for message in messages if query.casefold() in message['text'].casefold()]
        hits = [{key: value for key, value in message.items() if key != 'text'} |
                {'snippet': _snippet(message['text'], query)} for message in matches[:limit]]
        return {**base, 'query': query, 'hits': hits, 'has_more': len(matches) > limit, 'truncated': truncated}
    if scope == 'canvas':
        conversations = {}
        for message in messages:
            key = (message['node_id'], message['host'])
            if key not in conversations:
                current = nodes.get(key[0])
                conversations[key] = {'conversation_id': f'{key[0]}:{key[1]}', 'node_id': key[0], 'host': key[1],
                                      'retired': current is None, 'title': str((current or {}).get('title') or key[0])[:200],
                                      'last_message_id': message['message_id'], 'last_seq': message['seq'],
                                      'timestamp': message['timestamp'], 'snippet': _snippet(message['text']), 'message_count': 0}
            conversations[key]['message_count'] += 1
        rows = list(conversations.values())
        return {**base, 'conversations': rows[:limit], 'has_more': len(rows) > limit, 'truncated': truncated}
    before = args.get('before_seq')
    selected = sorted((message for message in messages if before is None or message['seq'] < before),
                      key=lambda message: message['seq'], reverse=True)
    page = selected[:limit]
    return {**base, 'messages': list(reversed(page)), 'has_more': len(selected) > limit,
            'next_before_seq': min((message['seq'] for message in page), default=None) if len(selected) > limit else None,
            'truncated': truncated}
