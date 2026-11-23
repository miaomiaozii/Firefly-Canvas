"""Connection-bound, RAM-only viewport receipts. No authoritative viewport cache."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import uuid
import time
import math
import threading
from collections.abc import Callable

from app.services.agent_errors import agent_error
from firefly_projects import ProjectStoreError
from app.project_sessions import session_project

TIMEOUT_MS = 3000
MAX_FRAME_BYTES = 4096
MAX_CONNECTIONS = 256
REJECTIONS = {
    'NODE_NOT_FOUND': (404, 'NODE_NOT_FOUND', '目标节点已不存在'),
    'DOCKED_TARGET': (400, 'INVALID_ARGUMENT', '目标节点已停靠，不能在画布 world 中定位'),
    'INVALID_ARGUMENT': (400, 'INVALID_ARGUMENT', '浏览器拒绝了视口参数'),
    'TARGET_NOT_RENDERABLE': (400, 'INVALID_ARGUMENT', '目标节点当前不可渲染'),
    'NOT_READY': (503, 'SERVER_NOT_READY', '浏览器画布尚未就绪'),
}


@dataclass
class Connection:
    socket: object
    project_id: str
    store: object
    generation: str
    loop: asyncio.AbstractEventLoop
    current_store: Callable[[], object]
    ready: bool = False
    pending: asyncio.Future | None = None
    command_id: str = ''
    command: str = ''


_connections: dict[object, Connection] = {}
_lock = threading.Lock()


async def receive(socket, text: str, current_store: Callable[[], object]) -> None:
    if len(text) > MAX_FRAME_BYTES or len(text.encode('utf-8')) > MAX_FRAME_BYTES:
        return
    try:
        message = json.loads(text)
    except (ValueError, RecursionError):
        return
    if not isinstance(message, dict):
        return
    project_id = session_project(socket)
    if not project_id or message.get('project_id') != project_id:
        return
    store = current_store()
    with _lock:
        connection = _connections.get(socket)
    if connection and connection.store is not store:
        disconnect(socket)
        await socket.close(code=1012, reason='Project library changed; reconnect canvas')
        return
    if message.get('type') == 'canvas_viewport_ready':
        if set(message) != {'type', 'project_id'}:
            return
        with _lock:
            connection = _connections.get(socket)
            if connection is None:
                if len(_connections) >= MAX_CONNECTIONS:
                    return
                # Lifecycle retirement must also cover handshakes awaiting disk validation (#485).
                connection = Connection(socket, project_id, store, uuid.uuid4().hex,
                                        asyncio.get_running_loop(), current_store)
                _connections[socket] = connection
        try:
            # Migration holds the store lock across filesystem copies; never stall socket timers.
            await asyncio.to_thread(store.agent_inverse_snapshot, project_id)
        except (ProjectStoreError, OSError, ValueError):
            disconnect(socket)
            return
        if store is not current_store():
            disconnect(socket)
            await socket.close(code=1012, reason='Project library changed; reconnect canvas')
            return
        with _lock:
            if _connections.get(socket) is not connection:
                return
            connection.ready = True
        await socket.send_json({'type': 'canvas_viewport_ready_ack',
                                'project_id': project_id, 'generation': connection.generation})
    elif message.get('type') == 'canvas_viewport_ack':
        with _lock:
            connection = _connections.get(socket)
        if connection and connection.pending and not connection.pending.done():
            if (message.get('command_id') == connection.command_id
                    and message.get('generation') == connection.generation
                    and message.get('command') == connection.command
                    and valid_receipt(message, connection.command)):
                connection.pending.set_result((message, time.time()))


def valid_receipt(message, command) -> bool:
    if not set(message) <= {'type', 'project_id', 'generation', 'command_id', 'command', 'status', 'viewport', 'reason'}:
        return False
    if message.get('status') == 'rejected':
        return isinstance(message.get('reason'), str) and message['reason'] in REJECTIONS
    return (message.get('status') == ('observed' if command == 'state' else 'applied')
            and valid_snapshot(message.get('viewport')))


def valid_snapshot(value) -> bool:
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'scale'}:
        return False
    try:
        return all(type(v) in (int, float) and math.isfinite(v) for v in value.values()) and value['scale'] > 0
    except OverflowError:
        return False


def disconnect(socket) -> None:
    with _lock:
        connection = _connections.pop(socket, None)
    if connection and connection.pending and not connection.pending.done():
        connection.pending.set_exception(unknown(connection.command_id))


async def watch(socket, current_store) -> None:
    # Library migration retains old files, so file existence alone cannot preserve authority.
    while True:
        await asyncio.sleep(0.05)
        with _lock:
            connection = _connections.get(socket)
        if connection and connection.store is not current_store():
            disconnect(socket)
            try:
                await socket.close(code=1012, reason='Project library changed; reconnect canvas')
            except (RuntimeError, OSError):
                pass
            return
        if connection and not (connection.store.root / connection.project_id / 'project.json').is_file():
            await invalidate_project(connection.store, connection.project_id)


async def invalidate_project(store, project_id: str) -> None:
    """Retire exact captured bindings, including pending requests, after a lifecycle move."""
    with _lock:
        connections = [c for c in _connections.values() if c.store is store and c.project_id == project_id]
    for connection in connections:
        if connection.loop is asyncio.get_running_loop():
            await _retire(connection)
        elif connection.loop.is_running():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(_retire(connection), connection.loop))


async def _retire(connection) -> None:
    with _lock:
        registered = _connections.get(connection.socket) is connection
    if registered:
        disconnect(connection.socket)


async def request_viewport(project_id: str, command: str, store, **arguments) -> dict:
    with _lock:
        candidates = [c for c in _connections.values() if c.ready and c.project_id == project_id and c.store is store]
    if not candidates:
        raise not_ready()
    if len(candidates) > 1:
        raise agent_error(409, 'CONFLICT',
                          f'检测到 {len(candidates)} 个打开的画布窗口，请关闭多余窗口后重试',
                          '关闭多余的同项目画布窗口后重试。',
                          details={'reason': 'MULTIPLE_READY_TABS', 'dispatched': False})
    connection = candidates[0]
    # Transport sends and receipt futures belong to the upgraded socket's loop.
    if connection.loop is not asyncio.get_running_loop():
        if not connection.loop.is_running():
            raise not_ready()
        future = asyncio.run_coroutine_threadsafe(_request_connection(connection, command, arguments), connection.loop)
        return await asyncio.wrap_future(future)
    return await _request_connection(connection, command, arguments)


async def _request_connection(connection, command, arguments):
    with _lock:
        registered = _connections.get(connection.socket) is connection
    if (not registered or session_project(connection.socket) != connection.project_id
            or connection.store is not connection.current_store()):
        raise not_ready()
    project_id, store = connection.project_id, connection.store
    if connection.pending is not None:
        raise agent_error(409, 'CONFLICT', '画布正在处理另一个视口请求',
                          '等待当前请求结束，再调用 viewport.state 查看真实视口。',
                          details={'reason': 'VIEWPORT_BUSY', 'dispatched': False})
    connection.command_id = uuid.uuid4().hex
    connection.command = command
    connection.pending = asyncio.get_running_loop().create_future()
    try:
        async with asyncio.timeout(TIMEOUT_MS / 1000):
            try:
                await connection.socket.send_json({'type': 'canvas_viewport_command',
                    'project_id': project_id, 'generation': connection.generation,
                    'command_id': connection.command_id, 'command': command, **arguments})
            except Exception as exc:
                raise unknown(connection.command_id) from exc
            while not connection.pending.done():
                if not (store.root / project_id / 'project.json').is_file():
                    disconnect(connection.socket)
                    break
                await asyncio.wait({connection.pending}, timeout=0.05)
            ack, received_at = connection.pending.result()
        # An ACK and a worker-thread library publication can race before the watcher wakes.
        if store is not connection.current_store():
            raise unknown(connection.command_id)
        if ack['status'] == 'rejected':
            reason = ack['reason']
            status, code, message = REJECTIONS[reason]
            raise agent_error(status, code, message,
                              '先检查目标节点与画布状态，再决定下一步。',
                              details={'reason': reason, 'command_id': connection.command_id})
        return {**ack['viewport'], 'updated_at': received_at, 'command_id': connection.command_id}
    except (TimeoutError, OSError, RuntimeError) as exc:
        raise unknown(connection.command_id) from exc
    finally:
        pending = connection.pending
        connection.pending = None
        if pending is not None:
            if not pending.done():
                pending.cancel()
            elif not pending.cancelled():
                pending.exception()


def not_ready():
    return agent_error(503, 'SERVER_NOT_READY', '画布视口尚未就绪',
                       '请先打开项目画布并等待加载完成。',
                       details={'reason': 'VIEWPORT_NOT_READY', 'dispatched': False})


def unknown(command_id: str):
    return agent_error(504, 'TOOL_TIMEOUT', '画布视口命令结果不明',
                       '先调用 viewport.state 读取真实视口，再决定下一步；不要自动重试。',
                       retryable=False, details={'outcome': 'unknown', 'command_id': command_id})
