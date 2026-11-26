"""One credential-free Responses route for the explicitly commissioned evaluation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock, Thread

import httpx

from tests.eval.trajectory.safety import digest
from tests.eval.trajectory.stub import Journal

ENDPOINT = 'http://192.168.10.147:8080/v1'
MODEL = 'flash-next-turbo-iq3s'
RESPONSES = '/v1/responses'
MANIFEST_SHA256 = 'c04dac9f6d7dad1a0b1e90af850bcd5d89bb12e307a0b37b88ca5f2c8619a219'
PRODUCT_RENDER = 'E0/renders/右侧.png'


def require_strata_fixture(manifest, render):
    if render != PRODUCT_RENDER or digest(manifest) != MANIFEST_SHA256:
        raise ValueError('Strata only permits the approved #404 E0 manifest and product render')


class ResponsesRelay:
    def __init__(self, root, *, limit, timeout, transport=None):
        self.journal = Journal(root / 'strata-relay.jsonl')
        self.limit, self.timeout, self.transport = limit, timeout, transport
        self.failure, self.count = None, 0
        self.lock = Lock()

    def __enter__(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(relay.timeout)

            def log_message(self, *_):
                pass

            def do_POST(self):
                relay.forward(self)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def forward(self, handler):
        if handler.path != RESPONSES:
            handler.send_error(404)
            return
        if any(handler.headers.get(name) is not None for name in ('Authorization', 'Proxy-Authorization', 'Cookie', 'X-API-Key')):
            self.journal.emit('relay_rejected', reason='credential header')
            handler.send_error(400)
            return
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            if (handler.headers.get_content_type() != 'application/json' or handler.headers.get('Transfer-Encoding')
                    or not 0 < length <= 32 * 1024 * 1024):
                raise ValueError('Unsupported request framing')
            body = handler.rfile.read(length)
            data = json.loads(body)
            if not isinstance(data, dict) or data.get('model') != MODEL or len(body) != length:
                raise ValueError('Wrong model or invalid body')
        except (ValueError, OSError):
            handler.send_error(400)
            return
        # Serialize upstream requests so an auth rejection latches before any other dispatch.
        with self.lock:
            if self.failure or self.count >= self.limit:
                self.failure = self.failure or 'Strata Responses request limit reached'
                handler.send_error(503)
                return
            self.count += 1
            self.journal.emit('responses_start', request=self.count, endpoint=ENDPOINT, model=MODEL)
            self._stream(handler, body)

    def _stream(self, handler, body):
        status = None
        try:
            # Fresh client, fixed URL, no inherited proxy/auth, redirects or upstream cookies.
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=self.timeout,
                              transport=self.transport) as client:
                with client.stream('POST', ENDPOINT + '/responses', content=body, headers={
                    'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json',
                }) as response:
                    status = response.status_code
                    self.journal.emit('responses_status', request=self.count, status=status)
                    if status in (401, 403):
                        self.failure = f'Strata rejected unauthenticated Responses (HTTP {status})'
                        handler.send_error(status)
                        return
                    if 300 <= status < 400:
                        self.failure = f'Strata Responses redirected (HTTP {status}); redirects forbidden'
                        handler.send_error(502)
                        return
                    handler.send_response(status)
                    handler.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                    handler.end_headers()
                    for chunk in response.iter_bytes():
                        handler.wfile.write(chunk)
                    self.journal.emit('responses_end', request=self.count)
        except (httpx.HTTPError, OSError) as exc:
            self.journal.emit('responses_transport_error', request=self.count, error_type=type(exc).__name__)
            if status is None:
                try:
                    handler.send_error(502)
                except OSError:
                    pass


def native_config(base_url):
    return ('model_provider="strata-evaluation"',
            'model_providers.strata-evaluation.name="Firefly evaluation relay"',
            f'model_providers.strata-evaluation.base_url={json.dumps(base_url)}',
            'model_providers.strata-evaluation.wire_api="responses"',
            'model_providers.strata-evaluation.requires_openai_auth=false',
            'model_providers.strata-evaluation.supports_websockets=false',
            'analytics.enabled=false')
