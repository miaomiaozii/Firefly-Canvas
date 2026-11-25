"""Private, restartable retrieval of already-generated image results (#493)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import re
from contextvars import ContextVar
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from firefly_json_io import write_json_atomic
from firefly_projects import InvalidMutation, ProjectNotFound, ProjectStoreError

MAX_BYTES = 1024 * 1024 * 1024
BACKOFF = (1, 3, 10)
EXPIRED = '结果链接已过期，无法取回'
_PRIVATE_DOWNLOAD = ContextVar('private_result_download', default=False)


class _PrivateDownloadLogFilter(logging.Filter):
    def filter(self, record):
        return not _PRIVATE_DOWNLOAD.get()


for _private_logger in ('httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy'):
    logging.getLogger(_private_logger).addFilter(_PrivateDownloadLogFilter())


class ResultLinkExpired(ProjectStoreError):
    pass


class ResultRecoveryPending(ProjectStoreError):
    pass


def provider_identity(provider, headers=None, *, model=None):
    # Only a digest is persisted; changed credentials/account/group must not query another account's task.
    identity = {k: provider.get(k) for k in
        ('id', 'base_url', 'image_base_url', 'protocol', 'type', 'group', 'channel', 'image_request_mode')}
    if model is not None:
        from app.core import effective_image_request_mode, effective_protocol
        identity.update(model=model, image_request_mode=effective_image_request_mode(provider, model),
                        protocol=effective_protocol(provider, model))
    identity['headers'] = dict(headers or {})
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def result_metadata(candidate, raw, received_at):
    value = candidate.get('value')
    metadata = dict(candidate)
    def visit(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, dict):
            if value and value in (obj.get('url'), obj.get('value'), obj.get('image_url')):
                metadata.update(obj)
            for item in obj.values():
                if isinstance(item, (dict, list)):
                    visit(item, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                visit(item, depth + 1)
    visit(raw)
    if isinstance(raw, dict):
        for key in ('expires_at', 'expiresAt', 'expires_in'):
            if key in raw:
                metadata.setdefault(key, raw[key])
    expiry = metadata.get('expires_at', metadata.get('expiresAt'))
    if expiry is None and isinstance(raw, dict):
        expiry = raw.get('expires_at', raw.get('expiresAt'))
    if expiry is not None:
        try:
            expiry = float(expiry)
            if expiry > 1e12:
                expiry /= 1000
        except (ValueError, TypeError):
            try:
                stamp = datetime.fromisoformat(str(expiry).replace('Z', '+00:00'))
                expiry = (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
            except ValueError:
                expiry = None
    if expiry is None and metadata.get('expires_in') is not None:
        try:
            expiry = received_at + float(metadata['expires_in'])
        except (TypeError, ValueError):
            expiry = None
    if expiry is not None and not math.isfinite(expiry):
        expiry = None
    expected = metadata.get('expected_bytes', metadata.get('content_length', metadata.get('size_bytes')))
    expected = int(expected) if str(expected or '').isdigit() else None
    return {'url': value, 'received_at': received_at, 'expires_at': expiry if expiry is not None else received_at + 86400,
            'expiry_estimated': expiry is None,
            'expected_bytes': expected, 'etag': '', 'sha256': str(metadata.get('sha256') or '').lower()}


async def stream_result(url, path, state, save, *, client_factory=None, resolve=None):
    """Only append a verified byte range of the same strong-ETag representation."""
    from app.services.image_transport import create_image_transport_client
    if resolve is None:
        from app.core import ensure_public_generation_target
        resolve = ensure_public_generation_target
    offset = path.stat().st_size if path.exists() else 0
    etag = state.get('etag', '')
    resumable = offset and re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag) and state.get('encoding', 'identity') == 'identity'
    async with create_image_transport_client(client_factory=client_factory, phase_tag='download',
                              timeout=httpx.Timeout(connect=20, read=90, write=60, pool=20),
                              follow_redirects=False, trust_env=False) as client:
        current = url
        for redirects in range(6):
            target, headers, extensions = await resolve(current)
            headers = {**headers, 'Accept-Encoding': 'identity'}
            if resumable:
                headers.update({'Range': f'bytes={offset}-', 'If-Range': etag})
            async with client.stream('GET', target, headers=headers, extensions=extensions) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('location')
                    if not location or redirects == 5:
                        raise ProjectStoreError('结果链接重定向过多')
                    current = urllib.parse.urljoin(current, location)
                    continue
                if response.status_code in {403, 404}:
                    raise ResultLinkExpired(EXPIRED)
                if response.status_code == 416:
                    path.unlink(missing_ok=True)
                    state['etag'] = ''
                    save()
                    raise ProjectStoreError('结果续传范围已失效')
                response.raise_for_status()
                encoding = response.headers.get('content-encoding', 'identity').lower()
                length = response.headers.get('content-length')
                if length is not None and not length.isdigit():
                    raise ProjectStoreError('结果长度声明无效')
                declared = int(length) if length is not None else None
                total = None
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                    if not resumable or not match or encoding != 'identity' or response.headers.get('etag') != etag:
                        path.unlink(missing_ok=True)
                        state.update(etag='', encoding='identity')
                        save()
                        raise ProjectStoreError('结果续传校验失败')
                    start, end, total = map(int, match.groups())
                    if (start != offset or end < start or end != total - 1
                            or (state.get('total_bytes') is not None and total != state['total_bytes'])
                            or (declared is not None and declared != end - start + 1)):
                        path.unlink(missing_ok=True)
                        raise ProjectStoreError('结果续传范围不匹配')
                    mode = 'ab'
                elif response.status_code == 200:
                    # An ignored Range is a full replacement, never an append.
                    offset = 0
                    mode = 'wb'
                    total = declared if encoding == 'identity' else None
                else:
                    raise ProjectStoreError('结果下载响应状态无效')
                raw_count = decoded_count = 0
                # num_bytes_downloaded is wire/compressed bytes; aiter_bytes yields decoded bytes.
                with path.open(mode) as handle:
                    state.update(etag=response.headers.get('etag', ''), encoding=encoding, total_bytes=total)
                    save()
                    async for chunk in response.aiter_bytes(64 * 1024):
                        decoded_count += len(chunk)
                        if offset + decoded_count > MAX_BYTES:
                            raise ProjectStoreError('在线模型返回的图片超过 1GB 限制')
                        handle.write(chunk)
                        state['downloaded_bytes'] = offset + decoded_count
                        save()
                    raw_count = response.num_bytes_downloaded
                size = path.stat().st_size
                if (declared is not None and raw_count != declared) or (total is not None and size != total):
                    raise ProjectStoreError('结果下载不完整')
                if state.get('expected_bytes') is not None and size != state['expected_bytes']:
                    path.unlink(missing_ok=True)
                    raise ProjectStoreError('结果大小与预期不符')
                return response
    raise ProjectStoreError('结果链接重定向过多')


class _AsyncClaim:
    # Generation executors and HTTP handlers use different event loops.
    def __init__(self):
        self.lock = threading.Lock()

    async def __aenter__(self):
        while not self.lock.acquire(blocking=False):
            await asyncio.to_thread(time.sleep, 0.02)
        return self

    async def __aexit__(self, *_args):
        self.lock.release()


class ResultDownloads:
    def __init__(self, root, app):
        self.root = Path(root)
        self.app = app
        self.guard = threading.RLock()
        self.locks = {}
        self.workers = set()
        self.resume_locks = {}
        self.store = app.FIREFLY_PROJECT_STORE
        self.closing = False
        self.active = {}

    def _worker(self):
        from contextlib import contextmanager

        @contextmanager
        def claim():
            task = asyncio.current_task()
            with self.guard:
                if self.closing:
                    raise asyncio.CancelledError
                owner = task not in self.active
                if owner:
                    self.active[task] = threading.Event()
            try:
                yield
            finally:
                if owner:
                    with self.guard:
                        self.active.pop(task).set()
        return claim()

    async def shutdown(self):
        # HTTP retrieval and generation archives can belong to different event loops.
        with self.guard:
            self.closing = True
            active = list(self.active.items())
        for task, _ in active:
            if not task.done():
                task.get_loop().call_soon_threadsafe(task.cancel)
        local = [task for task, _ in active if task.get_loop() is asyncio.get_running_loop()]
        if local:
            await asyncio.gather(*local, return_exceptions=True)
        for task, settled in active:
            if task.get_loop() is not asyncio.get_running_loop():
                await asyncio.to_thread(settled.wait)

    def _paths(self, history_id='generation-*'):
        if history_id != 'generation-*' and not re.fullmatch(r'generation-[a-f0-9]{32}', history_id):
            raise InvalidMutation('生成记录标识无效')
        for path in self.root.glob('*/private-result-downloads/' + history_id + '.json'):
            safe = self.app.FIREFLY_PROJECT_STORE.image_result_download_queue_path(path.parent.parent.name, path.stem)
            if Path(safe) == path.resolve():
                yield path

    def load(self, history_id, project_id=None):
        with self.guard:
            path = (Path(self.app.FIREFLY_PROJECT_STORE.image_result_download_queue_path(project_id, history_id))
                    if project_id is not None else next(self._paths(history_id), None))
            if path is None or not path.exists():
                raise FileNotFoundError(history_id)
            return json.loads(path.read_text(encoding='utf-8'))

    def save(self, queue):
        with self.guard:
            path = self.app.FIREFLY_PROJECT_STORE.image_result_download_queue_path(queue['project_id'], queue['history_id'])
            write_json_atomic(path, queue, keep_backup=False)

    def find_task(self, project_id, task_id):
        if not self.root.exists():
            return None
        for path in self._paths():
            queue = self.load(path.stem)
            if queue['project_id'] == project_id and queue['task_id'] == task_id:
                return queue
        return None

    def _projects_identity(self):
        return hashlib.sha256(str(self.app.FIREFLY_PROJECT_STORE.root.resolve()).casefold().encode()).hexdigest()

    def validate(self, queue, project_id):
        if queue['project_id'] != project_id or queue.get('projects_identity') != self._projects_identity():
            raise InvalidMutation('原项目库或生成记录已失效')
        store = self.app.FIREFLY_PROJECT_STORE
        with store._lock:
            canvas, history = store._image_result_download_owner(project_id, queue['node_id'],
                queue['task_id'], queue['history_id'], queue['generation'])
            queue['node_exists'] = any(n.get('id') == queue['node_id'] for n in canvas.get('nodes', []))
            slots = {i['id']: i for i in history.get('result_download_slots', (history.get('download') or {}).get('items', []))}
            for item in queue['items']:
                if item.get('url') and (item['id'] not in slots or any(
                        slots[item['id']].get(k) != item.get(k) for k in ('index', 'output_id'))):
                    raise InvalidMutation('原结果槽身份已失效')
            if str(queue['task_id']).startswith('agent_img_'):
                original = queue.get('history') or {}
                if any(not original.get(k) or original.get(k) != history.get(k)
                       for k in ('op_id', 'source_node_id')):
                    raise InvalidMutation('无法确认原 Agent 操作归属')
                from app.routers.api_project_library import durable_project_image_task
                from app.routers.api_agent_tools import recovered_generation_writer
                if recovered_generation_writer(project_id, durable_project_image_task(canvas, queue['task_id']) or {}) is None:
                    raise InvalidMutation('无法确认原 Agent 操作归属')

    def project_queues(self, project_id):
        folder = Path(self.app.FIREFLY_PROJECT_STORE.image_result_download_queue_path(
            project_id, 'generation-' + '0' * 32)).parent
        for path in folder.glob('generation-*.json'):
            try:
                queue = self.load(path.stem, project_id)
                self.validate(queue, project_id)
            except (ProjectNotFound, InvalidMutation):
                # Orphaned ownership is local to a group; corrupt queues and IO failures must stay visible.
                continue
            yield queue

    def sync_saved(self, queue, canvas):
        history = next((h for h in canvas.get('history', []) if h.get('id') == queue['history_id']), {})
        outputs = history.get('outputs') or []
        known = {i.get('output', {}).get('output_id') for i in queue['items'] if i.get('output')}
        if queue['items'] and all('index' not in i for i in queue['items']):
            # Legacy queues omitted inline results; only durable occurrence orders can fill that gap.
            saved = [o.get('output_order', n) for n, o in enumerate(outputs) if o.get('output_id') not in known]
            start = max(saved, default=-1) + 1
            for n, item in enumerate(queue['items']):
                item['index'] = item.get('output', {}).get('output_order', start + n)
        for position, output in enumerate(outputs):
            if output.get('output_id') in known:
                continue
            index = output.get('output_order', position)
            item = next((i for i in queue['items'] if i.get('index') == index and not i.get('output')), None)
            if item is None:
                item = {'id': uuid.uuid4().hex, 'index': index}
                queue['items'].append(item)
            item.update(output=copy.deepcopy(output), status='succeeded', error='')
        queue['items'].sort(key=lambda i: i.get('index', 0))
        return queue

    def public(self, queue):
        now = time.time()
        items = []
        for position, item in enumerate(queue['items']):
            status = 'succeeded' if item.get('output') else item.get('status', 'pending')
            if queue.get('status') == 'unavailable' and status != 'succeeded':
                status = 'unavailable'
            items.append({'id': item['id'], 'index': item.get('index', position),
                'output_id': item.get('output', {}).get('output_id') or item.get('output_id'),
                'status': status, 'remaining_seconds': max(0, int(item['expires_at'] - now)) if item.get('expires_at') else None,
                'expiry_estimated': item.get('expiry_estimated', False), 'error': item.get('error', ''),
                'can_retrieve': bool(item.get('url')) and status in {'pending', 'failed', 'expired'},
                'downloaded_bytes': item.get('downloaded_bytes'), 'total_bytes': item.get('total_bytes', item.get('expected_bytes')),
                'transport_metrics': copy.deepcopy(item.get('transport_metrics'))})
        active = [i for i in queue['items'] if not i.get('output') and i.get('expires_at')]
        expiry = min((i['expires_at'] for i in active), default=None)
        return {'history_id': queue['history_id'], 'project_id': queue['project_id'],
                'node_id': queue['node_id'], 'node_exists': queue.get('node_exists', True),
                'task_id': queue['task_id'], 'items': items,
                'status': queue.get('status', 'generated_pending_download'), 'expires_at': expiry,
                'remaining_seconds': max(0, int(expiry - now)) if expiry else None,
                'downloaded': sum(i['status'] == 'succeeded' for i in items),
                'total': len(items), 'error': queue.get('error', ''),
                'expiry_estimated': any(i.get('expiry_estimated', False) for i in active),
                'upstream_task_id': queue.get('upstream_task_id', '')}

    @staticmethod
    def select(queue, item_ids):
        if item_ids is None:
            return [i['id'] for i in queue['items'] if not i.get('output') and i.get('url')
                    and i.get('status') != 'unavailable']
        if (not isinstance(item_ids, list) or not item_ids or any(type(i) is not str for i in item_ids)
                or len(set(item_ids)) != len(item_ids)
                or not set(item_ids) <= {i['id'] for i in queue['items']}):
            raise InvalidMutation('结果标识无效或不属于本次生成')
        return [item_id for item_id in item_ids if next(i for i in queue['items'] if i['id'] == item_id).get('status') != 'unavailable']

    async def checkpoint(self, project_id, history, candidates, raw, provider, *, mutate=None):
        with self._worker():
            async with self.locks.setdefault(history['id'], _AsyncClaim()):
                return await self._checkpoint(project_id, history, candidates, raw, provider, mutate=mutate)

    async def _checkpoint(self, project_id, history, candidates, raw, provider, *, mutate=None):
        remote = [item for item in candidates if item.get('type', 'url') == 'url'
                  and str(item.get('value', '')).startswith(('http://', 'https://'))]
        if not remote:
            return {}
        from app.core import extract_task_id
        from app.services.providers import _soon_url_auto_provider
        history_id = history['id']
        model = history.get('actual_model') or (history.get('route') or {}).get('model') or history.get('model')
        if not isinstance(model, str) or not model.strip():
            raise InvalidMutation('原生成模型标识缺失')
        opened = await asyncio.to_thread(self.app.project_store_call, 'open_project', project_id)
        with self.guard:
            try:
                queue = self.load(history_id)
            except FileNotFoundError:
                invocation_provider = _soon_url_auto_provider(provider, model)
                queue = {'history_id': history_id, 'project_id': project_id, 'task_id': history['task_id'],
                         'node_id': history['result_node_id'], 'generation': uuid.uuid4().hex,
                         'provider_id': provider.get('id'), 'model': model,
                         'provider_identity': provider_identity(provider, self.app.api_headers(provider=provider)),
                         'model_identity': provider_identity(provider, self.app.api_headers(provider=provider, model=model), model=model),
                         # Keep saved-config guards separate from the invocation's account/auth/route.
                         'invocation_identity': provider_identity(invocation_provider,
                             self.app.api_headers(provider=invocation_provider, model=model), model=model),
                         'projects_identity': self._projects_identity(),
                         'upstream_task_id': (extract_task_id(raw) or '') if isinstance(raw, dict) else '',
                         'items': [], 'history': copy.deepcopy(history),
                         'status': 'generated_pending_download'}
            ids = {}
            now = time.time()
            upstream_id = (extract_task_id(raw) or '') if isinstance(raw, dict) else ''
            self.sync_saved(queue, opened['canvas'])
            start = max((i.get('index', n) for n, i in enumerate(queue['items'])), default=-1) + 1
            for output_index, candidate in enumerate(candidates):
                item_id = uuid.uuid4().hex
                index = start + output_index
                item = {'id': item_id, 'index': index, 'output_id': 'output-' + item_id,
                        'status': 'pending', 'upstream_task_id': upstream_id,
                        'upstream_output_index': output_index}
                if candidate in remote:
                    item.update(result_metadata(candidate, raw, now))
                # Inline siblings also own checkpoint slots; journal suspension must not rebind either kind.
                ids[id(candidate)] = item_id
                queue['items'].append(item)
            # URL/expiry checkpoint precedes every GET; only the server-private queue contains URLs.
            self.save(queue)
        write = mutate or self._mutate
        await write('checkpoint_image_result_download', project_id, queue['node_id'], queue['task_id'],
                    history, queue['generation'], self.public(queue))
        await self._mutate('flush', project_id)
        return ids

    def remember_saved(self, history_id, output, item_id):
        with self.guard:
            queue = self.load(history_id)
            item = next((i for i in queue['items'] if i['id'] == item_id), None)
            if item is None:
                raise InvalidMutation('原结果槽身份已失效')
            output.update(output_id=item['output_id'], output_order=item['index'])
            item.update(output=copy.deepcopy(output), status='succeeded', error='')
            self.save(queue)

    async def _mutate(self, *args, **kwargs):
        from app.services.agent_ops_async import run_blocking_to_completion
        write = self.app.project_store_call
        if len(args) > 3 and str(args[3]).startswith('agent_img_'):
            from app.routers.api_project_library import durable_project_image_task
            from app.routers.api_agent_tools import recovered_generation_writer
            opened = await asyncio.to_thread(write, 'open_project', args[1])
            durable = durable_project_image_task(opened['canvas'], args[3])
            write = recovered_generation_writer(args[1], durable) if durable else None
            if write is None:
                raise InvalidMutation('无法确认原 Agent 操作归属')
        # Cancellation must not let Store.close race a still-running retrieval write (#493).
        return await run_blocking_to_completion(write, *args, **kwargs)

    async def archive(self, history_id, item_id, *, refresh=None):
        with self._worker():
            return await self._archive(history_id, item_id, refresh=refresh)

    async def _archive(self, history_id, item_id, *, refresh=None):
        lock = self.locks.setdefault(history_id, _AsyncClaim())
        async with lock:
            queue = self.load(history_id)
            self.validate(queue, queue['project_id'])
            item = next(i for i in queue['items'] if i['id'] == item_id)
            if item.get('output'):
                return item['output']
            path = Path(await self._mutate('image_result_download_path', queue['project_id'], queue['node_id'],
                                          queue['task_id'], history_id, queue['generation'], item_id))
            from app.services.image_transport import _capture_image_transport, record_image_decode, _IMAGE_OBSERVATION_SINK
            outer_sink = _IMAGE_OBSERVATION_SINK.get()
            def observe(record):
                if outer_sink is not None:
                    try:
                        outer_sink(record)
                    except Exception:
                        pass
                item.setdefault('transport_metrics', []).append(record)
                item['transport_metrics'] = item['transport_metrics'][-128:]
                self.save(queue)
            for attempt in range(4):
                self.validate(queue, queue['project_id'])
                item.update(status='downloading' if attempt == 0 else 'retrying', error='')
                self.save(queue)
                try:
                    if not item.get('expiry_estimated') and item['expires_at'] <= time.time():
                        raise ResultLinkExpired(EXPIRED)
                    token = _PRIVATE_DOWNLOAD.set(True)
                    try:
                        with _capture_image_transport(observe):
                            response = await stream_result(item['url'], path, item, lambda: self.save(queue))
                    finally:
                        _PRIVATE_DOWNLOAD.reset(token)
                    from app.core import validate_generated_image, generated_image_filename
                    try:
                        started = time.perf_counter()
                        try:
                            width, height, mime = validate_generated_image(path)
                        finally:
                            record_image_decode(response, 'image', (time.perf_counter() - started) * 1000, path.stat().st_size)
                        digest = self._digest(path)
                        if item.get('sha256') and digest != item['sha256']:
                            raise ProjectStoreError('结果 SHA256 校验失败')
                    except ProjectStoreError:
                        path.unlink(missing_ok=True)
                        item['etag'] = ''
                        self.save(queue)
                        raise
                    record = await self._mutate('import_image_result_download', queue['project_id'], queue['node_id'],
                        queue['task_id'], history_id, queue['generation'], item_id,
                        generated_image_filename(item.get('index', queue['items'].index(item)), mime), mime, digest)
                    item['output'] = {'asset_id': record['asset_id'], 'kind': 'image', 'name': record['name'],
                                      'natural_w': width, 'natural_h': height,
                                      'output_id': item.get('output_id', 'output-' + item_id),
                                      'output_order': item.get('index', queue['items'].index(item))}
                    item.update(actual_sha256=digest, status='succeeded', error='', downloaded_bytes=path.stat().st_size)
                    self.save(queue)
                    path.unlink(missing_ok=True)
                    return item['output']
                except ResultLinkExpired:
                    item.update(status='recovering', error=EXPIRED)
                    self.save(queue)
                    try:
                        if refresh is None or item.get('refreshed'):
                            raise ResultLinkExpired(EXPIRED)
                        with _capture_image_transport(observe):
                            replacement = await refresh(queue, item)
                        item.update(replacement, refreshed=True, etag='')
                        path.unlink(missing_ok=True)
                        self.save(queue)
                    except ResultLinkExpired:
                        item.update(status='unavailable', error=EXPIRED)
                        self.save(queue)
                        raise ResultLinkExpired(EXPIRED) from None
                    except (httpx.HTTPError, ProjectStoreError, OSError):
                        item.update(status='expired', error='原任务查询暂不可用，可重新取回')
                        self.save(queue)
                        raise ResultRecoveryPending(item['error']) from None
                except (httpx.HTTPError, ProjectStoreError, OSError):
                    if attempt == 3:
                        item.update(status='failed', error='下载失败，可重新取回（不会重新生成、不扣费）')
                        self.save(queue)
                        raise ProjectStoreError(item['error']) from None
                if attempt < 3:
                    await asyncio.sleep(BACKOFF[attempt])
            raise ResultLinkExpired(EXPIRED)

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    async def failed(self, history_id, error):
        queue = self.load(history_id)
        opened = await asyncio.to_thread(self.app.project_store_call, 'open_project', queue['project_id'])
        self.sync_saved(queue, opened['canvas'])
        if queue['items'] and all(i.get('output') for i in queue['items']):
            # Every slot is archived: a later write error is not a retrieval failure, and marking
            # download_failed would strand the task with nothing left to retrieve.
            try:
                await self._complete(queue['project_id'], queue)
                queue.update(status='succeeded', error='')
                return queue
            except ProjectStoreError as exc:
                logging.getLogger(__name__).warning('Result retrieval completion deferred history=%s error=%s',
                                                    history_id, type(exc).__name__)
                queue.update(status='generated_pending_download', error='')
                self.save(queue)
                return queue
        queue.update(status='download_failed', error=EXPIRED if isinstance(error, ResultLinkExpired) else
                     '下载失败，可重新取回（不会重新生成、不扣费）')
        self.save(queue)
        await self._mutate('update_image_result_download', queue['project_id'], queue['node_id'], queue['task_id'],
                           history_id, queue['generation'], self.public(queue))
        return queue

    def discard(self, history_id):
        for path in self._paths(history_id):
            path.unlink(missing_ok=True)

    async def resume(self, project_id, history_id, *, refresh=None, item_ids=None):
        # Serialize selections, not requests: the second caller must eventually run its own subset.
        with self._worker():
            async with self.resume_locks.setdefault(history_id, _AsyncClaim()):
                return await self._resume(project_id, history_id, refresh=refresh, item_ids=item_ids)

    async def _resume(self, project_id, history_id, *, refresh=None, item_ids=None):
        if self.root.resolve() != self.app.FIREFLY_PROJECT_STORE.root.resolve():
            raise InvalidMutation('原项目库或生成记录已失效')
        try:
            queue = self.load(history_id, project_id)
        except FileNotFoundError:
            opened = await asyncio.to_thread(self.app.project_store_call, 'open_project', project_id)
            if any(h.get('id') == history_id and h.get('status') == 'succeeded' for h in opened['canvas'].get('history', [])):
                return {'history_id': history_id, 'status': 'succeeded'}
            raise
        try:
            self.validate(queue, project_id)
        except ProjectStoreError:
            return self.public({**queue, 'status': 'unavailable', 'error': '原节点或生成记录已失效'})
        selected = self.select(queue, item_ids)
        with self.guard:
            self.workers.add(history_id)
        try:
            queue.update(status='generated_pending_download', error='')
            self.save(queue)
            opened = await self._mutate('prepare_image_result_download', project_id, queue['node_id'], queue['task_id'],
                               history_id, queue['generation'], self.public(queue))
            self.sync_saved(queue, opened)
            self.save(queue)
            await self._mutate('update_image_result_download', project_id, queue['node_id'], queue['task_id'],
                               history_id, queue['generation'], self.public(queue))
            for item_id in selected:
                try:
                    await self.archive(history_id, item_id, refresh=refresh)
                except (ProjectStoreError, OSError, httpx.HTTPError):
                    pass
                queue = self.load(history_id, project_id)
                outputs = [i['output'] for i in queue['items'] if i.get('output')]
                await self._mutate('publish_image_result_download', project_id, queue['node_id'], queue['task_id'],
                                  history_id, queue['generation'], outputs, self.public(queue))
            queue = self.load(history_id, project_id)
            if any(not i.get('output') for i in queue['items']):
                errors = [i.get('error') for i in queue['items'] if i.get('error')]
                queue.update(status='download_failed' if errors else 'generated_pending_download', error=errors[0] if errors else '')
                self.save(queue)
                canvas = await self._mutate('update_image_result_download', project_id, queue['node_id'], queue['task_id'],
                                           history_id, queue['generation'], self.public(queue))
                await self._mutate('flush', project_id)
                await self.app.manager.broadcast_canvas_updated(project_id, int(canvas['updated_at']))
                return self.public(queue)
            return await self._complete(project_id, queue)
        except (ProjectStoreError, OSError, httpx.HTTPError) as exc:
            try:
                return self.public(await self.failed(history_id, exc))
            except ProjectStoreError:
                # Missing histories or unprovable ownership must never resurrect user-deleted state.
                queue.update(status='unavailable', error='原节点或生成记录已失效')
                self.save(queue)
                return self.public(queue)
        finally:
            with self.guard:
                self.workers.discard(history_id)

    async def _complete(self, project_id, queue):
        history_id = queue['history_id']
        outputs = [item['output'] for item in queue['items'] if item.get('output')]
        history = {**queue['history'], 'status': 'succeeded', 'outputs': outputs,
                   'completed_at': round(time.time() * 1000), 'download': {**self.public(queue), 'status': 'succeeded'}}
        canvas = await self._mutate('complete_image_result_download', project_id, queue['node_id'], queue['task_id'],
                                   history_id, queue['generation'], outputs, history)
        history = next(h for h in canvas.get('history', []) if h.get('id') == history_id)
        from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
        from app.routers.api_project_library import durable_project_image_result
        with CANVAS_TASK_LOCK:
            task = CANVAS_TASKS.get(queue['task_id'])
            if task and task.get('project_id') == project_id:
                task.update(status='succeeded', error='', updated_at=time.time(),
                    result=durable_project_image_result(project_id, canvas,
                        {'history_record': history, 'result_node_id': queue['node_id']}))
        await self._mutate('flush', project_id)
        self.discard(history_id)
        await self.app.manager.broadcast_canvas_updated(project_id, int(canvas['updated_at']))
        return {'history_id': history_id, 'status': 'succeeded'}

    async def startup(self, *, refresh=None):
        if self.root.exists():
            for path in self._paths():
                try:
                    queue = self.load(path.stem)
                    await self.resume(queue['project_id'], queue['history_id'], refresh=refresh)
                except (OSError, ValueError, ProjectStoreError) as exc:
                    logging.getLogger(__name__).warning('Result retrieval startup skipped history=%s error=%s',
                                                        path.stem, type(exc).__name__)


_instances = {}
_instances_guard = threading.Lock()


def downloads(app):
    root = str(app.FIREFLY_PROJECT_STORE.root)
    with _instances_guard:
        if root not in _instances or _instances[root].store is not app.FIREFLY_PROJECT_STORE:
            _instances[root] = ResultDownloads(root, app)
        return _instances[root]
