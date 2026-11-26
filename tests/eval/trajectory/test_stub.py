"""Deterministic I/O faults: these tests are NOT real model trajectories."""
import asyncio
import io
import socket
import subprocess
import sys

import httpx
from PIL import Image
import pytest

from tests.eval.trajectory.safety import REPO, isolated_env
from tests.eval.trajectory.stub import Faults, ImageBudget, MODELS, ORIGIN, ProviderFence, SafetyViolation, tiny_png


@pytest.mark.parametrize('scenario,expected', [
    ('normal', ['normal'] * 3), ('no_channel', ['no_channel', 'no_channel', 'normal']),
    ('transient', ['transient', 'normal', 'normal']), ('rate_limit', ['rate_limit', 'normal', 'normal']),
    ('quota_exhausted', ['quota_exhausted'] * 3), ('content_refusal', ['content_refusal', 'normal', 'normal']),
    ('size_mismatch', ['size_mismatch', 'normal', 'normal']),
])
def test_fixed_fault_recovery_sequence(scenario, expected):
    faults = Faults(scenario)
    assert [faults.next('same prompt', model)['injected'] for model in (MODELS[0], MODELS[0], MODELS[1])] == expected
    assert faults.next('other prompt', MODELS[0])['injected'] == 'normal'


def test_campaign_slots_and_hard_attempt_budget():
    faults = Faults('campaign', 6)
    assert [faults.next(str(i), MODELS[0])['injected'] for i in range(6)] == [
        'no_channel', 'transient', 'rate_limit', 'content_refusal', 'size_mismatch', 'quota_exhausted']
    with pytest.raises(SafetyViolation, match='budget'):
        faults.next('extra', MODELS[0])
    with pytest.raises(ValueError, match='Unknown'):
        Faults('silent-success')


def test_tiny_png_is_valid_real_media():
    with Image.open(io.BytesIO(tiny_png())) as image:
        image.load()
        assert image.size == (8, 8) and image.format == 'PNG'


@pytest.mark.parametrize('url,model', [('https://paid.example/v1/images/generations', MODELS[0]),
                                      (ORIGIN + '/v1/videos', MODELS[0]),
                                      (ORIGIN + '/v1/images/generations', 'unknown-generator')])
def test_zero_real_generation_and_unknown_generator_fail_closed(url, model):
    sent = []
    with ProviderFence() as fence:
        with httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r))) as client:
            with pytest.raises(SafetyViolation):
                client.post(url, json={'model': model, 'prompt': 'offline'})
        with pytest.raises(SafetyViolation, match='socket'):
            socket.socket().connect(('192.0.2.10', 443))
        with pytest.raises(SafetyViolation, match='DNS'):
            socket.getaddrinfo('paid.example', 443)
    assert sent == []
    assert any(row['kind'] == 'network_blocked' for row in fence.journal.rows)


def test_image_budget_stubs_the_eleventh_dispatch_even_after_a_provider_error(tmp_path):
    submitted = []

    def image_transport(request):
        submitted.append(request)
        if len(submitted) == 1:
            raise httpx.ReadTimeout('sent but outcome unknown', request=request)
        return httpx.Response(201, json={'provider': 'simulated'}, request=request)

    budget_file = tmp_path / 'budget.sqlite'
    ImageBudget.create(budget_file)
    with ProviderFence(image_budget=ImageBudget(budget_file), image_transport=image_transport) as fence, \
            httpx.Client(trust_env=False) as client:
        def send():
            return client.post(ORIGIN + '/v1/images/generations', json={'model': MODELS[0], 'prompt': 'retry'})

        with pytest.raises(httpx.ReadTimeout):
            send()
        assert [send().status_code for _ in range(9)] == [201] * 9
        overflow = send()

    assert len(submitted) == 10
    assert overflow.status_code == 200 and overflow.json()['data'][0]['b64_json']
    assert len([row for row in fence.journal.rows if row['kind'] == 'image_dispatch']) == 10
    assert any(row.get('image_route') == 'budget_exhausted' for row in fence.journal.rows)


def test_e0_scenarios_share_one_budget_across_fences_and_processes(tmp_path):
    submitted = []

    def image_transport(request):
        submitted.append(request)
        return httpx.Response(201, json={'provider': 'simulated'}, request=request)

    async def exercise(budget_file):
        url = ORIGIN + '/v1/images/generations'
        for scenario in ('normal', 'campaign'):
            with ProviderFence(scenario, image_budget=ImageBudget(budget_file), image_transport=image_transport) as fence:
                async with httpx.AsyncClient(trust_env=False) as client:
                    if scenario == 'campaign':
                        for i, status in enumerate((503, None, 429, 400, 400, 403)):
                            payload = {'model': MODELS[0], 'prompt': f'fault slot {i}'}
                            if status is None:
                                with pytest.raises(httpx.ConnectError):
                                    await client.post(url, json=payload)
                            else:
                                assert (await client.post(url, json=payload)).status_code == status
                        fallback = await client.post(url, json={'model': MODELS[1], 'prompt': 'fault slot 0'})
                        assert fallback.status_code == 200 and fallback.json()['data'][0]['b64_json']
                    responses = await asyncio.gather(*(client.post(url, json={'model': MODELS[0], 'prompt': 'healthy slot'})
                                                       for _ in range(15)))
                    assert sorted(response.status_code for response in responses) == [200] * 5 + [201] * 10
                    assert len([row for row in fence.journal.rows if row.get('image_route') == 'budget_exhausted']) == 5

    budget_file = tmp_path / 'budget.sqlite'
    ImageBudget.create(budget_file)
    previous = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        asyncio.run(exercise(budget_file))
    finally:
        asyncio.set_event_loop_policy(previous)
    assert len(submitted) == 20
    env, _ = isolated_env(tmp_path / 'new-process')
    result = subprocess.run([sys.executable, '-B', '-c',
                             'import sys; from tests.eval.trajectory.stub import ImageBudget; budget = ImageBudget(sys.argv[1]); '
                             'print(budget.reserve("normal"), budget.reserve("campaign"))', str(budget_file)],
                            env=env, cwd=REPO, check=True, capture_output=True, text=True, timeout=15)
    assert result.stdout.strip() == 'False False'
    with pytest.raises((FileExistsError, ValueError), match='NEW|exist'):
        ImageBudget.create(budget_file)


def test_only_single_image_payloads_can_use_a_budget_and_missing_budget_fails_closed(tmp_path):
    submitted = []

    def image_transport(request):
        submitted.append(request)
        return httpx.Response(201, request=request)

    budget_file = tmp_path / 'budget.sqlite'
    budget = ImageBudget.create(budget_file)
    data = {'model': MODELS[0], 'prompt': 'one image'}
    with ProviderFence(image_budget=budget, image_transport=image_transport) as fence, httpx.Client(trust_env=False) as client:
        url = ORIGIN + '/v1/images/generations'
        assert client.post(url, json=data | {'n': 2}).status_code == 200
        assert client.post(url, json=data | {'n': True}).status_code == 200
        assert client.post(url, json=data | {'extra_body': {'n': 2}}).status_code == 200
        assert client.post(url, json=data | {'n': 1}).status_code == 201
        edit = ORIGIN + '/v1/images/edits'
        files = {'image': ('product.png', tiny_png(), 'image/png')}
        assert client.post(edit, data=data | {'n': '2'}, files=files).status_code == 200
        assert client.post(edit, data=data | {'n': '1'}, files=files).status_code == 201
        with pytest.raises(SafetyViolation, match='Ambiguous'):
            client.post(url, headers={'Content-Type': 'application/json'},
                        content=b'{"model":"trajectory-image","prompt":"two counts","n":2,"n":1}')
        with pytest.raises(SafetyViolation, match='Unexpected budgeted image origin'):
            client.post(url + '?n=2', json=data)
        budget_file.unlink()
        with pytest.raises(SafetyViolation, match='budget unavailable'):
            client.post(url, json=data)
    assert len(submitted) == 2 and not budget_file.exists()
    assert len([row for row in fence.journal.rows if row.get('image_route') == 'unsupported_image_payload']) == 4


def test_preflight_and_patch_cleanup():
    send, connect = httpx.AsyncClient.send, socket.socket.connect
    fence = ProviderFence()
    with pytest.raises(SafetyViolation, match='not installed'):
        fence.preflight()
    with fence:
        assert fence.preflight()['installed']
        left, right = socket.socketpair()
        left.close()
        right.close()
    assert httpx.AsyncClient.send is send and socket.socket.connect is connect
    with pytest.raises(SafetyViolation, match='not installed'):
        fence.preflight()


def test_actual_async_send_multipart_and_error_headers():
    async def exercise():
        with ProviderFence('rate_limit') as fence:
            async with httpx.AsyncClient() as client:
                response = await client.post(ORIGIN + '/v1/images/edits', data={'model': MODELS[0], 'prompt': 'literal'},
                                             files={'image': ('product.png', tiny_png(), 'image/png')})
                assert response.status_code == 429 and response.headers['retry-after'] == '0'
                response = await client.post(ORIGIN + '/v1/images/generations', json={'model': MODELS[0], 'prompt': 'literal'})
                assert response.status_code == 200 and response.json()['data'][0]['b64_json']
            assert fence.journal.rows[0]['request']['reference_count'] == 1
            assert [r['kind'] for r in fence.journal.rows] == ['stub_request', 'stub_outcome'] * 2
    previous = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        asyncio.run(exercise())
    finally:
        asyncio.set_event_loop_policy(previous)
