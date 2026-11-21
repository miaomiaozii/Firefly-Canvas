"""Inject at HTTP send, not at tools/jobs/adapters; no real provider can escape."""
from __future__ import annotations

import base64
from contextlib import ExitStack
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
import socket
import struct
import threading
import time
from unittest.mock import patch
import zlib

from tests.eval.trajectory.safety import redact

ORIGIN = 'https://trajectory.invalid'
MODELS = ('trajectory-image', 'trajectory-fallback')
FAULTS = {'normal', 'no_channel', 'transient', 'rate_limit', 'quota_exhausted', 'content_refusal', 'size_mismatch'}


class SafetyViolation(RuntimeError):
    pass


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


def request_fields(request):
    content = request.content
    if request.headers.get('content-type', '').startswith('application/json'):
        body = json.loads(content)
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
            else:
                body[name] = part.get_payload(decode=True).decode('utf-8')
    else:
        raise SafetyViolation('Unexpected generation payload format')
    return {key: body[key] for key in ('model', 'prompt', 'size', 'quality', 'aspect_ratio', 'n') if key in body} | {
        'reference_count': len(refs), 'body_sha256': hashlib.sha256(content).hexdigest(), 'body_bytes': len(content)}


class ProviderFence:
    """Installed only within a context; HTTP plus socket guards also cover unexpected generators."""
    def __init__(self, scenario='normal', *, port=3194, limit=60, journal=None):
        self.faults, self.port = Faults(scenario, limit), port
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
        fields = request_fields(request)
        if fields.get('model') not in MODELS or not fields.get('prompt'):
            return self.deny('Unexpected generator model or empty prompt')
        outcome = self.faults.next(fields['prompt'], fields['model'])
        self.journal.emit('stub_request', method=request.method, path=request.url.path, request=fields, **outcome)
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
