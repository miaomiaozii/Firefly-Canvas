"""Opt-in E0 bridge: only ordinary image slots can reach the approved Soon account."""
import asyncio
import base64
from contextvars import ContextVar
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import json
import logging
from pathlib import Path
import re
from unittest.mock import patch

import httpx
from PIL import Image

from tests.eval.trajectory.safety import in_repository, register_owned_secrets, safe_path
from tests.eval.trajectory.soon import HOST, MODEL, SoonBudgetExhausted, SoonPostBudget
from tests.eval.trajectory.soon_smoke import SoonEgress, configured_soon
from tests.eval.trajectory.stub import ImageBudget, SafetyViolation

_ASSET_LIMIT = 20 * 1024 * 1024


def completed_smoke_ledger(path):
    path = safe_path(path)
    if path.name != 'image-budget.sqlite' or in_repository(path):
        raise SafetyViolation('Expected the original external smoke ledger')
    outcome = json.loads(safe_path(path.parent / 'smoke-result.json').read_text(encoding='utf-8'))
    budget = ImageBudget(path)
    if (outcome.get('provider') != HOST or outcome.get('model') != MODEL or outcome.get('images_returned') != 1
            or outcome.get('post_dispatches') != 1 or outcome.get('post_statuses') != [200]
            or budget.snapshot()['smoke'] != 1):
        raise SafetyViolation('Approved Soon smoke evidence is incomplete')
    return budget


def claim_soon_scenario(ledger, scenario):
    if scenario not in {'normal', 'campaign'}:
        raise SafetyViolation('Only the two approved E0 scenarios may use Soon')
    budget = completed_smoke_ledger(ledger)
    if budget.snapshot()[scenario]:
        raise SafetyViolation('An already billed scenario cannot be rerun')
    # Even a zero-POST native run consumes its one approved execution.
    with (Path(ledger).parent / f'soon-{scenario}.claimed').open('x', encoding='utf-8') as marker:
        marker.write('One E0 scenario execution claimed; do not retry on unknown outcome.\n')
    return budget


async def fetch_soon_asset(client, result):
    if result.get('type') == 'b64':
        encoded = result['value']
        if len(encoded) > (_ASSET_LIMIT + 2) // 3 * 4:
            raise SafetyViolation('Soon inline asset exceeds evaluation limit')
        content = base64.b64decode(encoded, validate=True)
    elif result.get('type') == 'url':
        try:
            url = httpx.URL(result['value'])
        except (TypeError, httpx.InvalidURL) as exc:
            raise SafetyViolation('Invalid Soon asset URL') from exc
        if url.scheme != 'https' or url.host != HOST or url.port not in (None, 443) or url.userinfo or url.fragment:
            return None
        async with client.stream('GET', url, follow_redirects=False) as response:
            if response.status_code != 200 or response.headers.get('content-type', '').split(';', 1)[0].strip().lower() not in {
                    'image/png', 'image/jpeg', 'image/webp'}:
                raise SafetyViolation('Soon asset did not return an approved image directly')
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                if len(buffer) + len(chunk) > _ASSET_LIMIT:
                    raise SafetyViolation('Soon asset exceeds evaluation limit')
                buffer.extend(chunk)
            content = bytes(buffer)
    else:
        raise SafetyViolation('Soon candidate returned no eligible image')
    if not content or len(content) > _ASSET_LIMIT:
        raise SafetyViolation('Invalid Soon asset size')
    with Image.open(io.BytesIO(content), formats=['PNG', 'JPEG', 'WEBP']) as image:
        if getattr(image, 'is_animated', False) or image.width * image.height > 20_000_000:
            raise SafetyViolation('Unsupported Soon image dimensions or animation')
        image.verify()
    return content


class SoonScenarioImages:
    def __init__(self, scenario, budget, journal, render_path, *, wire=None):
        self.scenario, self.budget, self.journal = scenario, budget, journal
        self.product = Path(render_path).read_bytes()
        self.wire = wire or (lambda: httpx.AsyncHTTPTransport(retries=0, http2=False, trust_env=False))
        self.scope = ContextVar('soon_scenario_client', default=None)
        self.gate = asyncio.Lock()
        self.egress = None

    def is_guarded_client(self, client):
        return self.egress is not None and self.egress.is_guarded_client(client)

    def permits_resolution(self, name, port):
        return self.egress is not None and self.egress.permits_resolution(name, port)

    def resolve(self, dns, host, port, *args, **kwargs):
        return self.egress.resolve(dns, host, port, *args, **kwargs)

    def is_resolved_address(self, address):
        return self.egress is not None and self.egress.is_resolved_address(address)

    def install(self, stack, main, generation, *, configured=configured_soon):
        profile, key = configured(main)
        if profile['id'] == 'trajectory-stub':
            raise SafetyViolation('Soon account must not share the inert provider ID')
        register_owned_secrets({'api_key': key})
        # HTTPX's info log contains the complete signed download URL and poll task ID.
        stack.enter_context(patch.object(logging.getLogger('httpx'), 'disabled', True))
        factory = generation._create_image_transport_client
        lookup = main.provider_env_key_value

        def generation_client(*args, phase_tag, **kwargs):
            scope = self.scope.get()
            if scope is None:
                return factory(*args, phase_tag=phase_tag, **kwargs)
            if phase_tag != 'generate' or scope['borrowed']:
                raise SafetyViolation('Unexpected Soon generation transport')
            scope['borrowed'] = True
            return scope['client']

        def approved_key(provider_id):
            if self.scope.get() is None:
                return lookup(provider_id)
            if provider_id != profile['id']:
                raise SafetyViolation('Unexpected Soon credential lookup')
            return key

        stack.enter_context(patch.object(generation, '_create_image_transport_client', generation_client))
        stack.enter_context(patch.object(main, 'provider_env_key_value', approved_key))
        self.profile, self.generation = profile, generation

    def references(self, request, fields):
        if not fields['reference_count']:
            return [] if request.url.path == '/v1/images/generations' else None
        if fields['reference_count'] != 1 or request.url.path != '/v1/images/edits':
            return None
        parts = BytesParser(policy=default).parsebytes(
            ('Content-Type: ' + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode()
            + request.content)
        files = [part for part in parts.iter_parts() if part.get_filename()]
        # No model-selected paths or URLs are allowed to leave the private project.
        if (len(files) != 1 or files[0].get_param('name', header='content-disposition') not in {'image', 'image[]'}
                or files[0].get_payload(decode=True) != self.product):
            return None
        return [{'url': 'data:image/png;base64,' + base64.b64encode(self.product).decode('ascii')}]

    async def __call__(self, request, fields, outcome):
        from app.services.image_transport import ImageTransportClient

        refs = self.references(request, fields)
        size = fields.get('size') or '1536x1024'
        ratio = fields.get('aspect_ratio')
        dimensions = re.fullmatch(r'([1-9][0-9]{0,3})x([1-9][0-9]{0,3})', size) if isinstance(size, str) else None
        if (refs is None or (size != 'auto' and (not dimensions or max(map(int, dimensions.groups())) > 4096))
                or ratio not in (None, '1:1', '4:3', '16:9')):
            return None, 'unsupported_soon_inputs'
        # The egress resolver override belongs to this one sequential evaluation request.
        async with self.gate:
            try:
                self.egress = SoonEgress(self.wire())
                boundary = SoonPostBudget(self.egress, budget=self.budget, scenario=self.scenario, journal=self.journal)
                client = ImageTransportClient(transport=boundary, trust_env=False, follow_redirects=False,
                                              max_upload_retries=0, timeout=httpx.Timeout(25, read=60))
                self.egress.client = client
                token = self.scope.set({'client': client, 'borrowed': False})
                try:
                    result, _ = await asyncio.wait_for(self.generation._generate_soon_url_candidate(
                        fields['prompt'], size, '', MODEL, refs, provider=self.profile,
                        image_parameters={'aspect_ratio': ratio} if ratio else None), 210)
                finally:
                    self.scope.reset(token)
                    await client.aclose()
                if not boundary.dispatched:
                    raise SafetyViolation('Soon result has no POST dispatch evidence')
                self.journal.emit('soon_image_receipt', slot=outcome['slot'], result_kind=result['type'])
                self.egress = SoonEgress(self.wire())
                async with httpx.AsyncClient(transport=self.egress, trust_env=False, follow_redirects=False,
                                             timeout=httpx.Timeout(25, read=60)) as download:
                    self.egress.client = download
                    image = await asyncio.wait_for(fetch_soon_asset(download, result), 90)
                if image is None:
                    return None, 'foreign_soon_asset'
                self.journal.emit('soon_image_material', slot=outcome['slot'], sha256=hashlib.sha256(image).hexdigest())
                return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image).decode('ascii')}]},
                                      request=request), None
            except SoonBudgetExhausted:
                return None, 'budget_exhausted'
            except Exception as exc:
                # Provider errors may embed credentials or signed URLs; do not pass them to the product journal.
                self.journal.emit('soon_image_failed', slot=outcome['slot'], error_type=type(exc).__name__)
                return httpx.Response(400, json={'error': {'code': 'content_policy_violation',
                                      'message': 'Soon evaluation image unavailable; no resubmission'}}, request=request), None
            finally:
                self.egress = None
