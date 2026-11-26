"""Inject at HTTP send, not at tools/jobs/adapters; no real provider can escape."""
from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack, closing
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import struct
import threading
import time
from unittest.mock import patch
import zlib

from tests.eval.trajectory.safety import external_new, redact, safe_path

ORIGIN = 'https://trajectory.invalid'
MODELS = ('trajectory-image', 'trajectory-fallback')
FAULTS = {'normal', 'no_channel', 'transient', 'rate_limit', 'quota_exhausted', 'content_refusal', 'size_mismatch'}


class SafetyViolation(RuntimeError):
    pass


class ImageBudget:
    """One durable reservation per possible real image, shared by both E0 scenarios."""

    COLUMNS = {'normal': 'normal_images', 'campaign': 'campaign_images'}
    PER_SCENARIO = 10
    TOTAL = 20

    def __init__(self, path):
        self.uri = safe_path(Path(path)).as_uri() + '?mode=rw'

    @classmethod
    def create(cls, path):
        path = external_new(Path(path))
        with path.open('xb'):
            pass
        budget = cls(path)
        with closing(sqlite3.connect(budget.uri, uri=True)) as db, db:
            db.execute('''CREATE TABLE image_budget (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                normal_images INTEGER NOT NULL CHECK (normal_images BETWEEN 0 AND 10),
                campaign_images INTEGER NOT NULL CHECK (campaign_images BETWEEN 0 AND 10),
                CHECK (normal_images + campaign_images <= 20))''')
            db.execute('INSERT INTO image_budget VALUES (1, 0, 0)')
        return budget

    def reserve(self, scenario):
        try:
            column = self.COLUMNS[scenario]
        except KeyError as exc:
            raise SafetyViolation('Real image budget only supports E0 normal and campaign') from exc
        # Commit before dispatch. A timeout may already have generated an image, so never refund it.
        try:
            with closing(sqlite3.connect(self.uri, uri=True, timeout=5)) as db, db:
                updated = db.execute(f'''UPDATE image_budget SET {column} = {column} + 1
                    WHERE id = 1 AND {column} < ? AND normal_images + campaign_images < ?''',
                                     (self.PER_SCENARIO, self.TOTAL))
                if not updated.rowcount and not db.execute('SELECT 1 FROM image_budget WHERE id = 1').fetchone():
                    raise SafetyViolation('E0 image budget is missing its run record')
                return updated.rowcount == 1
        except sqlite3.Error as exc:
            raise SafetyViolation('E0 image budget unavailable; no image dispatched') from exc


def tiny_png():
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 8, 8, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x33\x66\x99' * 8) * 8)) + chunk(b'IEND', b''))


def providers():
    return [{'id': 'trajectory-stub', 'name': 'B2 inert HTTP stub', 'enabled': True, 'primary': True,
             'protocol': 'openai', 'base_url': ORIGIN, 'api_key': 'inert-not-a-provider-key',
             'image_models': list(MODELS), 'video_models': [],
             'image_model_capabilities': {model: {'max_references': 20, 'arbitrary_size': True,
                                                 'resolutions': ['1k'], 'aspect_ratios': ['1:1', '4:3', '16:9']}
                                          for model in MODELS}}]


class Faults:
    """A fixed slot is the first distinct prompt at the actual I/O seam, not a model tool trace."""
    def __init__(self, scenario, limit=60):
        if scenario not in FAULTS | {'campaign'}:
            raise ValueError('Unknown fault scenario')
        self.scenario, self.limit = scenario, limit
        self.slots, self.attempts, self.count = {}, {}, 0
        self.lock = threading.Lock()

    def next(self, prompt, model):
        with self.lock:
            self.count += 1
            if self.count > self.limit:
                raise SafetyViolation('Provider attempt budget exceeded')
            key = hashlib.sha256(prompt.encode()).hexdigest()
            slot = self.slots.setdefault(key, len(self.slots) + 1)
            attempt = self.attempts[key] = self.attempts.get(key, 0) + 1
            fault = self.scenario if slot == 1 else 'normal'
            if self.scenario == 'campaign':
                fault = dict(enumerate(('no_channel', 'transient', 'rate_limit', 'content_refusal',
                                        'size_mismatch', 'quota_exhausted'), 1)).get(slot, 'normal')
            if fault == 'no_channel':
                outcome = fault if model == MODELS[0] else 'normal'
            elif fault == 'quota_exhausted':
                outcome = fault
            else:
                outcome = fault if attempt == 1 else 'normal'
            return {'slot': slot, 'attempt': attempt, 'prompt_sha256': key, 'injected': outcome,
                    'configured': fault, 'request_index': self.count}


class Journal:
    def __init__(self, path=None):
        self.path, self.rows, self.lock = path, [], threading.Lock()

    def emit(self, kind, **fields):
        with self.lock:
            row = redact({'index': len(self.rows) + 1, 'time': time.time(), 'kind': kind, **fields})
            self.rows.append(row)
            if self.path:
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            return row


def request_fields(request, *, budgeted=False):
    def unique_fields(pairs):
        body = dict(pairs)
        if len(body) != len(pairs):
            raise SafetyViolation('Ambiguous image request fields')
        return body

    content = request.content
    if request.headers.get('content-type', '').startswith('application/json'):
        body = json.loads(content, object_pairs_hook=unique_fields if budgeted else None)
        refs = body.get('image', body.get('images', []))
        refs = refs if isinstance(refs, list) else [refs]
    elif request.headers.get('content-type', '').startswith('multipart/form-data'):
        message = BytesParser(policy=default).parsebytes(
            ('Content-Type: ' + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + content)
        body, refs = {}, []
        for part in message.iter_parts():
            name = part.get_param('name', header='content-disposition')
            if part.get_filename():
                refs.append(hashlib.sha256(part.get_payload(decode=True)).hexdigest())
                if budgeted and name not in {'image', 'image[]', 'mask'}:
                    body[name] = None
            else:
                if budgeted and name in body:
                    raise SafetyViolation('Ambiguous image request fields')
                body[name] = part.get_payload(decode=True).decode('utf-8')
    else:
        raise SafetyViolation('Unexpected generation payload format')
    fields = {key: body[key] for key in ('model', 'prompt', 'size', 'quality', 'aspect_ratio', 'n') if key in body} | {
        'reference_count': len(refs), 'body_sha256': hashlib.sha256(content).hexdigest(), 'body_bytes': len(content)}
    if budgeted:
        count = body.get('n', 1)
        supported = {'model', 'prompt', 'size', 'quality', 'aspect_ratio', 'n', 'image', 'images', 'response_format'}
        fields['single_image_payload'] = (type(count) in (int, str) and count in (1, '1') and body.keys() <= supported)
    return fields


class ProviderFence:
    """Installed only within a context; HTTP plus socket guards also cover unexpected generators."""
    def __init__(self, scenario='normal', *, port=3194, limit=60, journal=None, image_budget=None, image_transport=None):
        if (image_budget is None) != (image_transport is None) or (image_transport is not None and not callable(image_transport)):
            raise ValueError('Image budget and one-shot transport must be supplied together')
        if image_budget is not None and scenario not in ImageBudget.COLUMNS:
            raise ValueError('Budgeted images only support E0 normal and campaign')
        self.faults, self.port = Faults(scenario, limit), port
        self.image_budget, self.image_transport = image_budget, image_transport
        self.journal = journal or Journal()
        self.stack = ExitStack()
        self.installed = False

    def deny(self, reason):
        self.journal.emit('network_blocked', reason=reason)
        raise SafetyViolation(reason)

    def response(self, request):
        import httpx
        if request.url.host != 'trajectory.invalid' or request.method != 'POST' or request.url.path not in (
                '/v1/images/generations', '/v1/images/edits'):
            return self.deny('Unexpected provider URL/method; zero real image/video network')
        if self.image_budget is not None and (request.url.scheme != 'https' or request.url.port is not None
                or request.url.query or request.url.fragment or request.url.userinfo):
            return self.deny('Unexpected budgeted image origin or URL parameters')
        fields = request_fields(request, budgeted=self.image_budget is not None)
        if fields.get('model') not in MODELS or not fields.get('prompt'):
            return self.deny('Unexpected generator model or empty prompt')
        outcome = self.faults.next(fields['prompt'], fields['model'])
        route = {}
        if self.image_budget is not None:
            if outcome['configured'] != 'normal':
                route['image_route'] = 'fault_scenario'
            elif not fields['single_image_payload']:
                route['image_route'] = 'unsupported_image_payload'
            elif not self.image_budget.reserve(self.faults.scenario):
                route['image_route'] = 'budget_exhausted'
            else:
                self.journal.emit('image_dispatch', scenario=self.faults.scenario, images=1,
                                  method=request.method, path=request.url.path, request=fields, **outcome)
                return self.image_transport(request)
        self.journal.emit('stub_request', method=request.method, path=request.url.path, request=fields, **outcome, **route)
        fault = outcome['injected']
        errors = {'no_channel': (503, 'model_not_found', '无可用渠道'),
                  'rate_limit': (429, 'rate_limit_exceeded', 'busy'),
                  'quota_exhausted': (403, 'quota_exhausted', 'quota exhausted'),
                  'content_refusal': (400, 'content_policy_violation', 'content policy rejected'),
                  'size_mismatch': (400, 'invalid_aspect_ratio', 'unsupported aspect ratio')}
        if fault == 'transient':
            self.journal.emit('stub_outcome', **outcome, status='connect_error')
            raise httpx.ConnectError('Connection error: injected before submission', request=request)
        status, body, headers = 200, {'data': [{'b64_json': base64.b64encode(tiny_png()).decode()}]}, {}
        if fault in errors:
            status, code, message = errors[fault]
            body = {'error': {'code': code, 'message': message}}
            headers = {'Retry-After': '0'} if status == 429 else {}
        self.journal.emit('stub_outcome', **outcome, status=status)
        return httpx.Response(status, json=body, headers=headers, request=request)

    def local(self, host, port):
        return str(host) in ('127.0.0.1', 'localhost', '::1') and port == self.port

    def __enter__(self):
        import httpx
        original_async, original_sync = httpx.AsyncClient.send, httpx.Client.send
        original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
        original_pair, pair_state = socket.socketpair, threading.local()
        fence = self

        async def async_send(client, request, **kwargs):
            if request.url.host == 'trajectory.invalid':
                await request.aread()
                if fence.image_budget is not None:
                    return await asyncio.to_thread(fence.response, request)
                return fence.response(request)
            if not fence.local(request.url.host, request.url.port):
                return fence.deny('Non-owned HTTP network blocked')
            return await original_async(client, request, **kwargs)

        def sync_send(client, request, **kwargs):
            if request.url.host == 'trajectory.invalid':
                request.read()
                return fence.response(request)
            if not fence.local(request.url.host, request.url.port):
                return fence.deny('Non-owned synchronous HTTP network blocked')
            return original_sync(client, request, **kwargs)

        def check(address):
            if (getattr(pair_state, 'active', False) and isinstance(address, tuple)
                    and str(address[0]) in ('127.0.0.1', '::1')):
                return
            if not isinstance(address, tuple) or not fence.local(address[0], address[1]):
                fence.deny('Non-owned socket network blocked')

        def socketpair(*args, **kwargs):
            pair_state.active = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                pair_state.active = False

        def connect(sock, address):
            check(address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            check(address)
            return original_connect_ex(sock, address)

        def dns(host, port, *args, **kwargs):
            if not fence.local(host.decode() if isinstance(host, bytes) else host, port):
                return fence.deny('Non-owned DNS blocked')
            return original_dns(host, port, *args, **kwargs)

        self.handlers = ((httpx.AsyncClient, 'send', async_send), (httpx.Client, 'send', sync_send),
                         (socket.socket, 'connect', connect), (socket.socket, 'connect_ex', connect_ex),
                         (socket, 'getaddrinfo', dns), (socket, 'socketpair', socketpair))
        for target, name, replacement in self.handlers:
            self.stack.enter_context(patch.object(target, name, replacement))
        self.installed = True
        return self

    def preflight(self):
        if not self.installed or any(getattr(target, name) is not replacement for target, name, replacement in self.handlers):
            raise SafetyViolation('Generation fence is not installed or was displaced')
        return {'installed': True, 'seam': 'httpx send before transport', 'provider_origin': ORIGIN,
                'all_other_external_network': 'blocked', 'models': list(MODELS)}

    def __exit__(self, *exc):
        self.installed = False
        return self.stack.__exit__(*exc)
