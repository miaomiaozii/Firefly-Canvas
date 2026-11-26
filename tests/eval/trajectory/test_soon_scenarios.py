import asyncio
import json
import subprocess
import sys

import httpx
import pytest

from tests.eval.trajectory.safety import REPO, isolated_env
from tests.eval.trajectory.soon import HOST, MODEL
from tests.eval.trajectory.soon_scenarios import claim_soon_scenario, fetch_soon_asset
from tests.eval.trajectory.stub import ImageBudget, Journal, ProviderFence, SafetyViolation, tiny_png


def test_scenario_routes_only_first_normal_attempt_through_approved_image_handler():
    journal, dispatched = Journal(), []

    async def approved(request, fields, outcome):
        dispatched.append(outcome['slot'])
        return httpx.Response(200, json={'data': [{'b64_json': 'real-in-memory-result'}]}, request=request), None

    async def exercise():
        with ProviderFence('rate_limit', journal=journal, async_image_route=approved):
            async with httpx.AsyncClient(trust_env=False) as client:
                async def submit(prompt):
                    return await client.post('https://trajectory.invalid/v1/images/generations',
                                             json={'model': 'trajectory-image', 'prompt': prompt, 'n': 1})

                assert (await submit('fault slot')).status_code == 429
                assert (await submit('fault slot')).status_code == 200
                assert (await submit('normal slot')).json()['data'][0]['b64_json'] == 'real-in-memory-result'
                assert (await submit('normal slot')).status_code == 400

    asyncio.run(exercise())
    assert dispatched == [2]
    assert [row['image_route'] for row in journal.rows if row['kind'] == 'stub_request'] == ['fault_scenario'] * 2


@pytest.mark.parametrize('url,status,expected', [
    (f'https://{HOST}/signed.png?sig=synthetic', 200, 'image'),
    (f'https://{HOST}/signed.png', 302, 'blocked'),
    (f'https://{HOST}:444/signed.png', 200, 'foreign'),
    (f'http://{HOST}/signed.png', 200, 'foreign'),
    ('https://other.invalid/signed.png', 200, 'foreign'),
    (f'https://user@{HOST}/signed.png', 200, 'foreign'),
])
def test_scenario_asset_only_fetches_direct_soon_https(url, status, expected):
    seen = []

    def provider(request):
        seen.append(request.url.host)
        assert request.method == 'GET' and 'authorization' not in request.headers
        return httpx.Response(status, headers={'location': 'https://other.invalid/leak', 'content-type': 'image/png'},
                              content=tiny_png())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider), follow_redirects=True) as client:
            if expected == 'blocked':
                with pytest.raises(SafetyViolation):
                    await fetch_soon_asset(client, {'type': 'url', 'value': url})
            else:
                assert await fetch_soon_asset(client, {'type': 'url', 'value': url}) == (
                    tiny_png() if expected == 'image' else None)

    asyncio.run(exercise())
    assert seen == ([] if expected == 'foreign' else [HOST])


def test_scenario_claim_requires_successful_smoke_and_blocks_second_zero_post_run(tmp_path):
    ledger = tmp_path / 'image-budget.sqlite'
    budget = ImageBudget.create(ledger)
    assert budget.reserve('smoke')
    outcome = tmp_path / 'smoke-result.json'
    outcome.write_text(json.dumps({'provider': HOST, 'model': MODEL, 'images_returned': 0, 'post_dispatches': 1,
                                   'post_statuses': [200]}), encoding='utf-8')
    with pytest.raises(SafetyViolation, match='smoke evidence'):
        claim_soon_scenario(ledger, 'normal')
    outcome.write_text(json.dumps({'provider': HOST, 'model': MODEL, 'images_returned': 1, 'post_dispatches': 1,
                                   'post_statuses': [200]}), encoding='utf-8')
    assert claim_soon_scenario(ledger, 'normal').snapshot()['normal'] == 0
    with pytest.raises(FileExistsError):
        claim_soon_scenario(ledger, 'normal')
    assert claim_soon_scenario(ledger, 'campaign').snapshot()['total'] == 1


def test_scenario_runs_product_candidate_with_only_pinned_reference_and_distinct_foreign_stub(tmp_path):
    env, _ = isolated_env(tmp_path)
    script = '''
import asyncio
import base64
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import httpx
from tests.eval.trajectory.soon_scenarios import HOST, MODEL, SoonScenarioImages
from tests.eval.trajectory.stub import ImageBudget, Journal, ProviderFence, providers, tiny_png

root = Path(sys.argv[1])
render = root / 'product.png'
render.write_bytes(tiny_png())
budget = ImageBudget.create(root / 'ledger.sqlite')
assert budget.reserve('smoke')
journal = Journal()
seen = []
emit = journal.emit

def capture(kind, **fields):
    if kind == 'soon_image_failed':
        raise AssertionError('Synthetic candidate failure') from sys.exception()
    return emit(kind, **fields)

journal.emit = capture

async def upstream(request):
    assert request.url.host == HOST
    seen.append((request.method, request.url.path))
    if request.method == 'POST':
        body = json.loads(request.content)
        assert request.headers['Authorization'] == 'Bearer synthetic-scenario-key'
        assert body['n'] == 1 and body['model'] == MODEL
        assert base64.b64decode(body['image'][0].split(',', 1)[1]) == render.read_bytes()
        if body['prompt'] == 'foreign':
            url = 'https://other.invalid/private.png'
        else:
            url = f'https://{HOST}/signed.png?sig=not-in-evidence'
        assert budget.snapshot()['normal'] in (1, 2)
        return httpx.Response(200, json={'data': [{'url': url}]})
    assert request.url.path == '/signed.png' and 'Authorization' not in request.headers
    return httpx.Response(200, headers={'content-type': 'image/png'}, content=tiny_png())

async def exercise():
    images = SoonScenarioImages('normal', budget, journal, render, wire=lambda: httpx.MockTransport(upstream))
    with ExitStack() as stack:
        stack.enter_context(ProviderFence('normal', journal=journal, async_image_route=images, egress=images))
        import main
        from app.services import generation
        profile = {**providers()[0], 'id': 'test-soon', 'base_url': f'https://{HOST}/v1', 'image_models': [MODEL],
                   'model_protocols': {MODEL: 'openai'}, 'model_request_modes': {MODEL: 'openai-json'}}
        images.install(stack, main, generation, configured=lambda _: (profile, 'synthetic-scenario-key'))
        async with httpx.AsyncClient(trust_env=False) as client:
            for prompt, ref in [('same', render.read_bytes()), ('foreign', render.read_bytes()), ('other reference', b'other')]:
                result = await client.post('https://trajectory.invalid/v1/images/edits',
                    files={'image': ('product.png', ref, 'image/png')}, data={'model': 'trajectory-image', 'prompt': prompt})
                assert result.status_code == 200, journal.rows
                assert result.json()['data'][0]['b64_json']
    assert seen == [('POST', '/v1/images/generations'), ('GET', '/signed.png'), ('POST', '/v1/images/generations')]
    assert [row['image_route'] for row in journal.rows if row['kind'] == 'stub_request'] == [
        'foreign_soon_asset', 'unsupported_soon_inputs']
    assert sum(row['kind'] == 'soon_image_material' for row in journal.rows) == 1
    assert all(value not in json.dumps(journal.rows) for value in ('synthetic-scenario-key', 'not-in-evidence', 'private.png'))

asyncio.run(exercise())
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)], cwd=REPO, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout


def test_scenario_carries_same_origin_image_through_real_batch_and_canvas(tmp_path):
    env, _ = isolated_env(tmp_path / 'outer')
    script = '''
from contextlib import nullcontext
import hashlib
from io import BytesIO
from pathlib import Path
import sys

import httpx
from PIL import Image
import pytest
from tests.eval.trajectory.live import canvas_image_hashes
from tests.eval.trajectory.soon_scenarios import HOST, MODEL, SoonScenarioImages
from tests.eval.trajectory.stub import ImageBudget, providers, tiny_png
from tests.eval.trajectory.test_pipeline import application, exercise_pipeline

root = Path(sys.argv[1])
render = root / 'product.png'
render.write_bytes(tiny_png())
image = BytesIO()
Image.new('RGB', (8, 8), '#0937f5').save(image, format='PNG')
material = image.getvalue()
budget = ImageBudget.create(root / 'ledger.sqlite')
assert budget.reserve('smoke')
seen = []

async def wire(request):
    seen.append(request.method)
    if request.method == 'POST':
        body = request.read()
        assert b'"image":["data:image/png;base64,' in body
        return httpx.Response(200, json={'data': [{'url': f'https://{HOST}/signed.png'}]})
    return httpx.Response(200, headers={'content-type': 'image/png'}, content=material)

class SyntheticSoon(SoonScenarioImages):
    def install(self, stack, main, generation):
        profile = {**providers()[0], 'id': 'synthetic-soon', 'base_url': f'https://{HOST}/v1',
                   'model_protocols': {MODEL: 'openai'}, 'model_request_modes': {MODEL: 'openai-json'}}
        super().install(stack, main, generation, configured=lambda _: (profile, 'synthetic-pipeline-key'))

with pytest.MonkeyPatch.context() as patch:
    fixture = application.__wrapped__(root, patch, autonomy='standard', images_factory=lambda journal:
        SyntheticSoon('normal', budget, journal, render, wire=lambda: httpx.MockTransport(wire)))
    try:
        app = next(fixture)
        result = exercise_pipeline(app, 'normal', 1, None, 'succeeded', 'standard', media_fence=lambda _: nullcontext())
        client, store, _, pid, _, journal, _ = app
        proof = [row['sha256'] for row in journal.rows if row['kind'] == 'soon_image_material']
        assert result['stub_requests'] == 0 and seen == ['POST', 'GET']
        assert proof == [hashlib.sha256(material).hexdigest()]
        canvas = store.canvas(pid)
        jobs = result['final_jobs']

        async def read_results():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                         base_url='http://127.0.0.1:3194', trust_env=False) as media:
                return await canvas_image_hashes(media, pid, canvas, jobs)

        archived = client.portal.call(read_results)
        assert [row['sha256'] for row in archived] == proof, (archived, proof)
    finally:
        fixture.close()
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)], cwd=REPO, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=70)
    assert result.returncode == 0, result.stderr + result.stdout
