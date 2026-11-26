"""One explicitly approved Soon image, in a fresh credential-isolated evaluation child."""
from contextvars import ContextVar
import asyncio
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import httpx

from tests.eval.trajectory.safety import clean_private, external_new, isolated_env, safe_path, save
from tests.eval.trajectory.soon import HOST, MODEL, SoonPostBudget
from tests.eval.trajectory.stub import ImageBudget, Journal, ProviderFence, SafetyViolation

_SETTINGS = Path('D:/Firefly-Canvas/data/api_providers.json')
_ENV_FILE = Path('D:/Firefly-Canvas/API/.env')
_PROMPT = 'A flat blue circle centered on a plain white background. One simple square image, no text.'


class SoonEgress(httpx.AsyncBaseTransport):
    """Permit TLS sockets only inside the checked request and its exact DNS resolution."""

    def __init__(self, upstream):
        self.upstream = upstream
        self.client = None
        self.resolved = ContextVar('soon_approved_resolution', default=None)

    def is_guarded_client(self, client):
        return client is self.client

    def permits_resolution(self, name, port):
        return self.resolved.get() is not None and name == HOST and port == 443

    def resolve(self, dns, host, port, *args, **kwargs):
        records = []
        for row in dns(host, port, *args, **kwargs):
            ip = ipaddress.ip_address(row[4][0])
            if ip.is_global and not ip.is_multicast:
                records.append(row)
        if not records:
            raise SafetyViolation('Soon DNS did not resolve to a public address')
        self.resolved.get().update(row[4][:2] for row in records)
        return records

    def is_resolved_address(self, address):
        return (self.resolved.get() is not None and isinstance(address, tuple)
                and address[:2] in self.resolved.get())

    async def handle_async_request(self, request):
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def scoped_dns(host, port, *, family=0, type=0, proto=0, flags=0):
            name = host.decode() if isinstance(host, bytes) else host
            if self.permits_resolution(name, port):
                # asyncio's default resolver executor does not propagate ContextVars.
                return await asyncio.to_thread(socket.getaddrinfo, host, port, family, type, proto, flags)
            return await original(host, port, family=family, type=type, proto=proto, flags=flags)

        token = self.resolved.set(set())
        try:
            with patch.object(loop, 'getaddrinfo', scoped_dns):
                return await self.upstream.handle_async_request(request)
        finally:
            self.resolved.reset(token)

    async def aclose(self):
        await self.upstream.aclose()


def configured_soon(main):
    from app.services.providers import _soon_url_auto_provider, _soon_url_verified_target, read_api_env_value

    entries = json.loads(safe_path(_SETTINGS).read_text(encoding='utf-8'))
    matches = [row for row in entries if isinstance(row, dict) and row.get('enabled') is True
               and MODEL in (row.get('image_models') or []) and _soon_url_verified_target(row, MODEL)]
    if len(matches) != 1:
        raise SafetyViolation('A unique configured Soon provider/model is required')
    profile = main.normalize_provider(matches[0])
    if not profile.get('id') or not _soon_url_verified_target(profile, MODEL):
        raise SafetyViolation('The configured Soon profile is not the approved one')
    with patch.object(main, 'API_ENV_FILE', str(safe_path(_ENV_FILE))):
        key = read_api_env_value(main.provider_key_env(profile['id']))
    if not key or key.startswith('dpapi:'):
        raise SafetyViolation('Configured Soon credential unavailable; no fallback')
    # Keep the credential in memory and outside product settings and all evidence files.
    profile = {name: value for name, value in profile.items()
               if name not in {'api_key', 'wallet_api_key', 'volcengine_secret_access_key'}}
    return _soon_url_auto_provider(profile, MODEL), key


async def generate_one(generation, main, profile, key, egress, budget, journal):
    from app.services.image_transport import ImageTransportClient

    boundary = SoonPostBudget(egress, budget=budget, scenario='smoke', journal=journal)
    client = ImageTransportClient(transport=boundary, trust_env=False, follow_redirects=False,
                                  max_upload_retries=0, timeout=httpx.Timeout(25, read=60))
    egress.client = client
    borrowed = False

    def generation_client(*args, phase_tag, **kwargs):
        nonlocal borrowed
        if phase_tag != 'generate' or borrowed:
            raise SafetyViolation('Unexpected Soon candidate client')
        borrowed = True
        return client

    def only_approved_key(provider_id):
        if provider_id != profile['id']:
            raise SafetyViolation('Unexpected Soon credential lookup')
        return key

    try:
        with (patch.object(generation, '_create_image_transport_client', generation_client),
              patch.object(main, 'provider_env_key_value', only_approved_key)):
            result, _ = await asyncio.wait_for(generation._generate_soon_url_candidate(
                _PROMPT, '1024x1024', '', MODEL, provider=profile), 210)
        if result.get('type') not in {'url', 'b64'} or not result.get('value') or budget.snapshot()['smoke'] != 1:
            raise SafetyViolation('No image from the authorized dispatch')
        return result['type']
    finally:
        await client.aclose()


def smoke_child(root):
    from tests.eval.trajectory.backend import assert_isolated

    assert_isolated(root)
    journal = Journal(root / 'soon-events.jsonl')
    budget = ImageBudget(root / 'image-budget.sqlite')
    egress = SoonEgress(httpx.AsyncHTTPTransport(retries=0, http2=False, trust_env=False))
    details = {'images_returned': 0, 'asset_downloaded': False}
    try:
        with (ProviderFence(journal=journal, egress=egress) as fence,
              patch.object(subprocess, 'Popen', side_effect=SafetyViolation('Smoke subprocess blocked'))):
            import main
            from app.services import generation

            fence.preflight()
            profile, key = configured_soon(main)
            details['result_kind'] = asyncio.run(generate_one(generation, main, profile, key, egress, budget, journal))
        details['images_returned'] = 1
    except Exception as exc:
        # Provider exceptions can include a body, task ID, signed URL or credentials.
        details['error_type'] = type(exc).__name__
        status = getattr(exc, 'status_code', None)
        if type(status) is int:
            details['http_status'] = status
    details['provider'] = HOST
    details['model'] = MODEL
    details['budget'] = budget.snapshot()
    details['post_dispatches'] = sum(row['kind'] == 'soon_submit' for row in journal.rows)
    details['post_statuses'] = [row['status'] for row in journal.rows if row['kind'] == 'soon_submit_outcome']
    details['poll_statuses'] = [row['status'] for row in journal.rows if row['kind'] == 'soon_poll']
    save(root / 'smoke-result.json', details)
    return 0 if details['images_returned'] == 1 else 2


def run_smoke(output_root):
    root = external_new(output_root)
    root.mkdir(parents=True, exist_ok=False)
    save(root / 'run-owner.json', {'kind': 'soon-smoke'})
    env, _ = isolated_env(root)
    budget = ImageBudget.create(root / 'image-budget.sqlite')
    result = 2
    try:
        child = subprocess.run([sys.executable, '-B', '-m', 'tests.eval.trajectory.soon_smoke', str(root)],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=245)
        result = child.returncode
    except subprocess.TimeoutExpired:
        save(root / 'smoke-timeout.json', {'budget': budget.snapshot(), 'outcome': 'unknown_no_retry'})
    finally:
        save(root / 'cleanup.json', clean_private(root))
    print(root / 'smoke-result.json')
    return result


if __name__ == '__main__':
    raise SystemExit(smoke_child(Path(sys.argv[1]).resolve()))
