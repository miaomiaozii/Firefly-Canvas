"""Deterministic I/O faults: these tests are NOT real model trajectories."""
import asyncio
import io
import socket

import httpx
from PIL import Image
import pytest

from tests.eval.trajectory.stub import Faults, MODELS, ORIGIN, ProviderFence, SafetyViolation, tiny_png


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
