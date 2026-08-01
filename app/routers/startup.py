"""Router: /startup domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import time
import base64
import shutil
import uuid
import hashlib
import mimetypes
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import (Any, Dict, LLM_MESSAGE_MAX_LENGTH, List, Optional, PROJECT_AGENT_RECOVERY_LOCK, PROJECT_CONVERSATION_READY_RESULTS, PROJECT_CONVERSATION_TASKS, PROJECT_CONVERSATION_TASKS_LOCK, ProjectNotFound, ProjectStoreError, Tuple, _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _agent_recovery_payload, _os, _sniff_image_ext, _sniff_image_ext_bytes, _sys, asyncio, conversation_error_message, conversation_node_by_id, conversation_task_snapshot, copy, forget_project_conversation_ready_result, forget_project_conversation_task, json, logging, os, project_conversation_ready_result, project_lifecycle_lock, re, remember_project_conversation_ready_result, shutil, urllib, uuid)
from app.core import _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _agent_recovery_payload, _sniff_image_ext, _sniff_image_ext_bytes
_from_main_names = ['ASSET_LIBRARY_DIR', 'LOCAL_UPLOAD_DIR', 'PROJECT_CONVERSATION_MAX_PENDING', 'PROJECT_CONVERSATION_MAX_PER_NODE', 'PROJECT_CONVERSATION_MAX_PER_PROJECT', '_agent_mark_recovered_uncertain', '_agent_submit_task', 'conversation_pending_task_keys', 'forget_project_conversation_durable', 'load_asset_library', 'load_project_conversation_durable', 'merge_project_conversation_result', 'now_ms', 'persist_project_conversation_uncertain', 'project_conversation_adapter', 'project_conversation_durable_path', 'project_store_call', 'run_project_conversation_task', 'save_asset_library', 'save_project_conversation_durable', 'schedule_project_conversation_task', 'unique_asset_category_dir']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class ProjectConversationTaskRunner:
    """Keep conversation work owned by the application lifecycle, not a request."""
    WORKER_COUNT = 4

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.queue: Optional[asyncio.Queue] = None
        self.workers: List[asyncio.Task] = []
        self.handles: set[asyncio.Future] = set()
        self.accepting = False
        self.node_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

    async def start(self) -> None:
        if self.accepting:
            return
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.accepting = True
        self.workers = [asyncio.create_task(self._worker_loop(), name=f'firefly-conversation-worker-{index}') for index in range(self.WORKER_COUNT)]

    def admission_error(self, project_id: str, node_id: str, request_id: str, *, canvas: Optional[Dict[str, Any]]=None, pending_keys: Optional[set[Tuple[str, str, str]]]=None) -> Optional[Dict[str, Any]]:
        task_key = (str(project_id), str(node_id), str(request_id))
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and (not current.done()):
                return None
            active = {key for key, value in PROJECT_CONVERSATION_TASKS.items() if not value.done()}
        pending = pending_keys if pending_keys is not None else _app_main.conversation_pending_task_keys(project_id, canvas=canvas, include_all_projects=True)
        combined = (active | pending) - {task_key}
        if len(combined) >= _app_main.PROJECT_CONVERSATION_MAX_PENDING:
            return {'code': 'conversation_queue_global_full', 'message': '对话队列已满，请稍后重试'}
        if sum((key[0] == task_key[0] for key in combined)) >= _app_main.PROJECT_CONVERSATION_MAX_PER_PROJECT:
            return {'code': 'conversation_queue_project_full', 'message': '该项目的对话队列已满，请稍后重试'}
        if sum((key[:2] == task_key[:2] for key in combined)) >= _app_main.PROJECT_CONVERSATION_MAX_PER_NODE:
            return {'code': 'conversation_queue_node_full', 'message': '该对话节点的待处理消息已达上限'}
        return None

    def submit(self, project_id: str, node_id: str, request_id: str, *, adapter=None, allow_persisted_overage: bool=False, pending_keys: Optional[set[Tuple[str, str, str]]]=None) -> bool:
        loop = self.loop
        queue = self.queue
        if not self.accepting or loop is None or queue is None:
            return False
        try:
            if asyncio.get_running_loop() is not loop:
                return False
        except RuntimeError:
            return False
        task_key = (str(project_id), str(node_id), str(request_id))
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and (not current.done()):
                return False
            if current is not None:
                PROJECT_CONVERSATION_TASKS.pop(task_key, None)
            active_keys = {key for key, value in PROJECT_CONVERSATION_TASKS.items() if not value.done()}
        pending_keys = pending_keys if pending_keys is not None else _app_main.conversation_pending_task_keys(project_id)
        combined_keys = active_keys | pending_keys
        current_is_persisted = task_key in combined_keys
        if len(combined_keys) > _app_main.PROJECT_CONVERSATION_MAX_PENDING and (not allow_persisted_overage) if current_is_persisted else len(combined_keys) >= _app_main.PROJECT_CONVERSATION_MAX_PENDING:
            return False
        project_count = sum((key[0] == task_key[0] for key in combined_keys))
        project_limit_reached = project_count > _app_main.PROJECT_CONVERSATION_MAX_PER_PROJECT and (not allow_persisted_overage) if current_is_persisted else project_count >= _app_main.PROJECT_CONVERSATION_MAX_PER_PROJECT
        if project_limit_reached:
            return False
        node_count = sum((key[:2] == task_key[:2] for key in combined_keys))
        node_limit_reached = node_count > _app_main.PROJECT_CONVERSATION_MAX_PER_NODE and (not allow_persisted_overage) if current_is_persisted else node_count >= _app_main.PROJECT_CONVERSATION_MAX_PER_NODE
        if node_limit_reached:
            return False
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and (not current.done()):
                return False
            handle = loop.create_future()
            PROJECT_CONVERSATION_TASKS[task_key] = handle
            self.handles.add(handle)
        try:
            queue.put_nowait((task_key, handle, adapter, 0))
        except Exception:
            with PROJECT_CONVERSATION_TASKS_LOCK:
                if PROJECT_CONVERSATION_TASKS.get(task_key) is handle:
                    PROJECT_CONVERSATION_TASKS.pop(task_key, None)
            self.handles.discard(handle)
            handle.cancel()
            return False
        return True

    async def _worker_loop(self) -> None:
        queue = self.queue
        assert queue is not None
        while True:
            item = await queue.get()
            task_key, handle, adapter = item[:3]
            retry_count = int(item[3]) if len(item) > 3 else 0
            retain_for_retry = False
            try:
                node_key = task_key[:2]
                node_lock = self.node_locks.setdefault(node_key, asyncio.Lock())
                async with node_lock:
                    retain_for_retry = not await _app_main.run_project_conversation_task(*task_key, adapter=adapter)
            except asyncio.CancelledError:
                if not handle.done():
                    handle.cancel()
                raise
            except Exception as exc:
                merged = None
                merge_error = None
                try:
                    merged = await _app_main.merge_project_conversation_result(*task_key, error=exc)
                except Exception as merge_exc:
                    merge_error = merge_exc
                    logging.getLogger(__name__).exception('conversation task failure could not be persisted: %s', task_key)
                if merged is None:
                    persisted = await asyncio.to_thread(persist_project_conversation_uncertain, *task_key, error=merge_error or exc)
                    if persisted or not isinstance(merge_error, ProjectNotFound):
                        retain_for_retry = True
                        if self.accepting and self.queue is queue and (not handle.done()):
                            await asyncio.sleep(min(0.025 * 2 ** min(retry_count, 4), 0.4))
                            if self.accepting and self.queue is queue and (not handle.done()):
                                queue.put_nowait((task_key, handle, adapter, retry_count + 1))
                    elif not handle.done():
                        handle.set_result(None)
                elif not handle.done():
                    handle.set_result(None)
            else:
                if retain_for_retry:
                    await asyncio.sleep(min(0.025 * 2 ** min(retry_count, 4), 0.4))
                    if self.accepting and self.queue is queue and (not handle.done()):
                        queue.put_nowait((task_key, handle, adapter, retry_count + 1))
                    else:
                        retain_for_retry = False
                elif not handle.done():
                    handle.set_result(None)
            finally:
                if not retain_for_retry:
                    forget_project_conversation_task(*task_key, handle=handle)
                    self.handles.discard(handle)
                    if self.accepting:
                        schedule_project_conversation_task(*task_key, adapter=adapter, recover_fifo=True)
                queue.task_done()
            if not self.accepting:
                return

    async def stop(self) -> None:
        self.accepting = False
        current_loop = asyncio.get_running_loop()
        workers = list(self.workers)
        current_workers = []
        foreign_waiters = []
        unreachable_workers = []
        for worker in workers:
            if worker.done():
                continue
            worker_loop = worker.get_loop()
            if worker_loop is current_loop:
                worker.cancel()
                current_workers.append(worker)
            elif worker_loop.is_running() and (not worker_loop.is_closed()):
                completion = current_loop.create_future()

                def mark_done(_worker, waiter=completion):

                    def resolve():
                        if not waiter.done():
                            waiter.set_result(None)
                    try:
                        current_loop.call_soon_threadsafe(resolve)
                    except RuntimeError:
                        pass

                def cancel_on_owner(target=worker, callback=mark_done):
                    if target.done():
                        callback(target)
                        return
                    target.add_done_callback(callback)
                    target.cancel()
                try:
                    worker_loop.call_soon_threadsafe(cancel_on_owner)
                    foreign_waiters.append(completion)
                except RuntimeError:
                    completion.cancel()
                    unreachable_workers.append(worker)
            else:
                unreachable_workers.append(worker)
        if current_workers:
            await asyncio.gather(*current_workers, return_exceptions=True)
        if foreign_waiters:
            await asyncio.gather(*foreign_waiters, return_exceptions=True)
        unfinished = [worker for worker in unreachable_workers if not worker.done()]
        if unfinished:
            raise RuntimeError('对话 worker 所属事件循环不可用，runner 状态已保留且未完成关闭')
        with PROJECT_CONVERSATION_TASKS_LOCK:
            for task_key, handle in list(PROJECT_CONVERSATION_TASKS.items()):
                if handle in self.handles:
                    if not handle.done():
                        handle_loop = handle.get_loop()
                        if handle_loop is current_loop:
                            handle.cancel()
                        elif handle_loop.is_running() and (not handle_loop.is_closed()):
                            try:
                                handle_loop.call_soon_threadsafe(handle.cancel)
                            except RuntimeError:
                                pass
                    PROJECT_CONVERSATION_TASKS.pop(task_key, None)
        self.handles.clear()
        self.workers = []
        self.queue = None
        self.loop = None
        self.node_locks.clear()

def migrate_asset_library_into_dirs():
    """一次性整理：给所有图片分组（含默认的角色/场景）补上真实文件夹，并把仍在 library/ 根目录的
    素材文件搬进各自分组的文件夹、同步更新 URL。幂等：已经在子文件夹里的不动；可安全反复执行。"""
    try:
        lib = load_asset_library()
    except Exception as exc:
        print(f'资产库分组整理：加载失败 {exc}')
        return
    changed = False
    for library in lib.get('libraries', []) or []:
        for cat in library.get('categories', []) or []:
            if (cat.get('type') or 'image') != 'image':
                continue
            if not cat.get('dir'):
                cat['dir'] = unique_asset_category_dir(library, cat.get('name') or '分组')
                changed = True
            cat_dir = str(cat.get('dir') or '').strip('/').strip()
            if not cat_dir:
                continue
            try:
                os.makedirs(os.path.join(ASSET_LIBRARY_DIR, cat_dir), exist_ok=True)
            except Exception as exc:
                print(f'资产库分组整理：建文件夹失败 {exc}')
                continue
            for item in cat.get('items') or []:
                raw_url = urllib.parse.unquote(str(item.get('url') or '').split('?', 1)[0])
                m = re.match('^/assets/library/([^/]+)$', raw_url)
                if not m:
                    continue
                fname = m.group(1)
                src = os.path.join(ASSET_LIBRARY_DIR, fname)
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(ASSET_LIBRARY_DIR, cat_dir, fname)
                try:
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                    item['url'] = '/assets/library/' + urllib.parse.quote(f'{cat_dir}/{fname}', safe='/')
                    changed = True
                except Exception as exc:
                    print(f'资产库分组整理：搬运 {fname} 失败 {exc}')
    if changed:
        try:
            save_asset_library(lib)
        except Exception as exc:
            print(f'资产库分组整理：保存失败 {exc}')

def migrate_double_extension_uploads():
    """修复历史遗留的双重扩展名（如 foo.png.png）：去掉重复的一层，并同步重命名 caption/classification 旁车文件。
    旧版 URL 导入会把自带扩展名的 entry.name 又拼一次 ext，导致文件名重复后缀、URL 对不上而无法显示。"""
    if not os.path.isdir(_app_main.LOCAL_UPLOAD_DIR):
        return
    renamed = 0
    for current, _dirs, files in os.walk(_app_main.LOCAL_UPLOAD_DIR):
        for name in files:
            m = _DOUBLE_EXT_RE.search(name)
            if not m or m.group(1).lower() not in _DOUBLE_EXT_MEDIA:
                continue
            old_path = os.path.join(current, name)
            new_path = os.path.join(current, name[:-len(m.group(1))])
            if os.path.exists(new_path):
                continue
            try:
                os.rename(old_path, new_path)
            except OSError:
                continue
            renamed += 1
            old_base = os.path.splitext(old_path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.exists(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if renamed:
        print(f'修复双重扩展名素材: {renamed} 个')

def _sniff_image_ext(path):
    try:
        with open(path, 'rb') as f:
            return _sniff_image_ext_bytes(f.read(16))
    except OSError:
        return None

def migrate_mislabeled_image_extensions():
    """有些采集来的图片内容与扩展名不符（例如 WebP 内容却叫 .png），导致服务端按错误 content-type 返回、
    严格的客户端（PS UXP）解不出来。这里按真实魔数纠正扩展名，并同步重命名 caption/classification 旁车。"""
    if not os.path.isdir(_app_main.LOCAL_UPLOAD_DIR):
        return
    img_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    fixed = 0
    for current, _dirs, files in os.walk(_app_main.LOCAL_UPLOAD_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in img_exts:
                continue
            path = os.path.join(current, name)
            real = _sniff_image_ext(path)
            if not real:
                continue
            if real == ext or (real == '.jpg' and ext == '.jpeg'):
                continue
            new_name = os.path.splitext(name)[0] + real
            new_path = os.path.join(current, new_name)
            if os.path.exists(new_path):
                continue
            try:
                os.rename(path, new_path)
            except OSError:
                continue
            fixed += 1
            old_base = os.path.splitext(path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.isfile(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if fixed:
        print(f'纠正图片扩展名(内容与后缀不符): {fixed} 个')

def recover_project_agent_tasks() -> int:
    """Rehydrate confirmed queued Agent tasks after an app reload.

    The canvas is authoritative.  Queued work is submitted with its stable
    task ID.  A running task may already have reached the provider when the
    process died, so it is closed as ``uncertain`` rather than blindly retried
    and potentially billed twice.
    """
    recovered = 0
    with PROJECT_AGENT_RECOVERY_LOCK:
        try:
            projects = _app_main.project_store_call('list_projects')
        except Exception:
            return 0
        for project in projects or []:
            project_id = str(project.get('id') or '')
            if not project_id:
                continue
            try:
                opened = _app_main.project_store_call('open_project', project_id)
            except Exception:
                continue
            canvas = opened.get('canvas') or {}
            for agent in canvas.get('nodes') or []:
                if not isinstance(agent, dict) or str(agent.get('type') or '') != 'smart-agent':
                    continue
                plan = agent.get('agent_plan')
                if not isinstance(plan, dict) or str(plan.get('status') or '') not in {'running', 'confirmed'}:
                    continue
                for node in canvas.get('nodes') or []:
                    if not isinstance(node, dict) or str(node.get('type') or '') != 'agent-task':
                        continue
                    if str(node.get('agent_id') or '') != str(agent.get('id') or '') or str(node.get('status') or '') not in {'queued', 'running'}:
                        continue
                    if str(node.get('status') or '') == 'running':
                        _agent_mark_recovered_uncertain(project_id, str(agent.get('id') or ''), plan, node)
                        continue
                    task = _agent_recovery_payload(node, plan)
                    if not task:
                        continue
                    execution_id = str(task.get('execution_id') or node.get('execution_id') or '')
                    if _agent_submit_task(task, project_id, str(agent.get('id') or ''), execution_id):
                        recovered += 1
    return recovered

def project_conversation_ready_result(project_id: str, node_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        ready = PROJECT_CONVERSATION_READY_RESULTS.get(task_key)
        return copy.deepcopy(ready) if ready is not None else None

def load_project_conversation_durable(project_id: str, node_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    with project_lifecycle_lock(str(project_id)):
        path = project_conversation_durable_path(project_id, node_id, request_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ProjectStoreError('对话任务恢复记录损坏') from exc
    if not isinstance(value, dict) or any((str(value.get(key) or '') != expected for key, expected in (('project_id', str(project_id)), ('node_id', str(node_id)), ('request_id', str(request_id))))):
        raise ProjectStoreError('对话任务恢复记录不匹配')
    return value

def save_project_conversation_durable(project_id: str, node_id: str, request_id: str, state: Dict[str, Any]) -> None:
    with project_lifecycle_lock(str(project_id)):
        path = project_conversation_durable_path(project_id, node_id, request_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = project_conversation_durable_path(project_id, node_id, request_id)
        payload = {'project_id': str(project_id), 'node_id': str(node_id), 'request_id': str(request_id), 'updated_at': _app_main.now_ms(), **copy.deepcopy(state)}
        temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        try:
            with open(temp, 'w', encoding='utf-8', newline='\n') as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

def persist_project_conversation_uncertain(project_id: str, node_id: str, request_id: str, *, error: Exception) -> bool:
    """Persist a non-retryable-in-place marker when the worker failure path breaks."""
    try:
        opened = _app_main.project_store_call('open_project', project_id)
        snapshot = conversation_task_snapshot(opened['canvas'], node_id, request_id)
        user_message_id = str((snapshot or {}).get('message', {}).get('id') or '')
        message = conversation_error_message(error)
        ready = {'user_message_id': user_message_id, 'error': f'对话任务状态不确定，暂不自动重试：{message}'}
        save_project_conversation_durable(project_id, node_id, request_id, {'state': 'ready', 'ready': ready})
        remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        return True
    except (ProjectNotFound, ProjectStoreError, OSError, ValueError):
        return False

def remember_project_conversation_ready_result(project_id: str, node_id: str, request_id: str, ready: Dict[str, Any]) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        PROJECT_CONVERSATION_READY_RESULTS[task_key] = copy.deepcopy(ready)

async def run_project_conversation_task(project_id: str, node_id: str, request_id: str, *, adapter=None) -> bool:
    user_message_id = ''
    ready = project_conversation_ready_result(project_id, node_id, request_id)
    if ready is None:
        durable = await asyncio.to_thread(load_project_conversation_durable, project_id, node_id, request_id)
        if durable and durable.get('state') == 'ready' and isinstance(durable.get('ready'), dict):
            ready = copy.deepcopy(durable['ready'])
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        elif durable and durable.get('state') == 'dispatching':
            ready = {'user_message_id': str(durable.get('user_message_id') or ''), 'error': '上次调用在服务中断前已发往服务商，结果状态不确定；为避免重复计费，本次不会自动重试，请确认账单后使用新请求显式重试'}
            await asyncio.to_thread(save_project_conversation_durable, project_id, node_id, request_id, {'state': 'ready', 'ready': ready})
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        current_canvas = opened['canvas']
        conversation = conversation_node_by_id(current_canvas, node_id)
        if not isinstance(conversation, dict):
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(_app_main.forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        messages = [item for item in conversation.get('messages') or [] if isinstance(item, dict)]
        user_index = next((index for index, item in enumerate(messages) if item.get('role') == 'user' and str(item.get('request_id') or '') == request_id), None)
        if user_index is None:
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(_app_main.forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        user_message = messages[user_index]
        user_message_id = str(user_message.get('id') or '')
        if user_message.get('status') != 'pending':
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(_app_main.forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        if ready is not None and str(ready.get('user_message_id') or '') != user_message_id:
            ready = copy.deepcopy(ready)
            ready['user_message_id'] = user_message_id
        earlier_pending = next((item for item in messages[:user_index] if item.get('role') == 'user' and item.get('status') == 'pending' and str(item.get('request_id') or '')), None)
        if earlier_pending is not None:
            schedule_project_conversation_task(project_id, node_id, str(earlier_pending['request_id']), adapter=adapter, recover_fifo=True)
            return False
        if ready is None:
            adapter_config = conversation.get('conversation_adapter') if isinstance(conversation.get('conversation_adapter'), dict) else user_message.get('adapter') if isinstance(user_message.get('adapter'), dict) else {}
            adapter_request = {'project_id': project_id, 'node_id': node_id, 'request_id': request_id, 'message': str(user_message.get('text') or ''), 'messages': copy.deepcopy(messages[:user_index + 1]), 'current_message_id': str(user_message.get('id') or ''), 'references': copy.deepcopy(user_message.get('references') or []), 'provider': str(adapter_config.get('provider') or 'comfly'), 'model': str(adapter_config.get('model') or ''), 'ms_model': str(adapter_config.get('ms_model') or ''), 'system_prompt': str(adapter_config.get('system_prompt') or '')}
            conversation_adapter = adapter if adapter is not None else _app_main.project_conversation_adapter
            await asyncio.to_thread(save_project_conversation_durable, project_id, node_id, request_id, {'state': 'dispatching', 'user_message_id': user_message_id})
            adapter_result = await conversation_adapter(adapter_request)
            if isinstance(adapter_result, str):
                assistant_text = adapter_result.strip()
                adapter_meta = {}
            elif isinstance(adapter_result, dict):
                assistant_text = str(adapter_result.get('text') or adapter_result.get('content') or '').strip()
                adapter_meta = adapter_result
            else:
                assistant_text = ''
                adapter_meta = {}
            if not assistant_text:
                raise RuntimeError('对话适配器返回了空回复')
            assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'status': 'completed', 'request_id': request_id, 'text': assistant_text[:LLM_MESSAGE_MAX_LENGTH], 'references': [], 'created_at': _app_main.now_ms()}
            if adapter_meta.get('model'):
                assistant_message['model'] = str(adapter_meta['model'])[:160]
            ready = {'user_message_id': user_message_id, 'assistant': assistant_message}
            await asyncio.to_thread(save_project_conversation_durable, project_id, node_id, request_id, {'state': 'ready', 'ready': ready})
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        merged = await _app_main.merge_project_conversation_result(project_id, node_id, request_id, user_message_id=str(ready.get('user_message_id') or user_message_id), assistant=ready.get('assistant'), error=RuntimeError(str(ready['error'])) if ready.get('error') else None)
    except Exception as exc:
        if ready is not None:
            return False
        if ready is None:
            ready = {'user_message_id': user_message_id, 'error': conversation_error_message(exc)}
            await asyncio.to_thread(save_project_conversation_durable, project_id, node_id, request_id, {'state': 'ready', 'ready': ready})
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        merged = await _app_main.merge_project_conversation_result(project_id, node_id, request_id, user_message_id=user_message_id, error=exc)
    if merged is None:
        return False
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(_app_main.forget_project_conversation_durable, project_id, node_id, request_id)
    return True