import asyncio

import httpx
import pytest
from fastapi import HTTPException

from tests.eval.trajectory.soon import SoonBudgetExhausted, SoonPostBudget
from tests.eval.trajectory.stub import ImageBudget, Journal, SafetyViolation
from tests.test_soon_url_default_493 import (
    MODEL, PROMPT, MemoryNetwork, no_outbound, pipeline, ref, runtime, soon_reference_png, transport,
)


def test_candidate_413_does_not_submit_the_smaller_reference_again(tmp_path, runtime, monkeypatch):
    budget = ImageBudget.create(tmp_path / 'soon.sqlite')
    journal = Journal()
    wire = MemoryNetwork([(413, {'error': 'too large'})] * 2)
    original = transport.ImageTransportClient.__init__

    def use_memory_wire(client, **kwargs):
        upstream = httpx.AsyncHTTPTransport(retries=0)
        upstream._pool._network_backend = wire
        guarded = SoonPostBudget(upstream, budget=budget, scenario='normal', journal=journal)
        original(client, **{**kwargs, 'transport': guarded, 'trust_env': False})

    monkeypatch.setattr(transport.ImageTransportClient, '__init__', use_memory_wire)
    provider = {**runtime['_provider'], 'model_protocols': {MODEL: 'openai'},
                'model_request_modes': {MODEL: 'openai-json'}}
    with pytest.raises((HTTPException, SafetyViolation)) as caught:
        asyncio.run(runtime['_generate_soon_url_candidate'](PROMPT, '4096x3072', '', MODEL,
            [ref(soon_reference_png())], provider=provider))

    sends = wire.requests()
    assert len(sends) == 1
    assert sends[0][0].startswith('POST /v1/images/generations?async=true ')
    assert sends[0][1]['n'] == 1
    assert isinstance(caught.value, SafetyViolation)
    assert budget.snapshot() == {'normal': 1, 'campaign': 0, 'smoke': 0, 'total': 1}
    assert [row['kind'] for row in journal.rows] == ['soon_submit', 'soon_submit_outcome', 'soon_submit_blocked']
    assert journal.rows[1]['status'] == 413
    assert journal.rows[2]['reason'] == 'repeat_post'


def test_smoke_is_capped_at_one_and_shares_the_twenty_post_limit(tmp_path):
    budget_file = tmp_path / 'shared.sqlite'
    budget = ImageBudget.create(budget_file)
    assert budget.reserve('smoke')
    assert not budget.reserve('smoke')
    other = ImageBudget(budget_file)
    assert all(other.reserve('normal') for _ in range(10))
    assert not other.reserve('normal')
    assert all(other.reserve('campaign') for _ in range(9))
    assert not other.reserve('campaign')
    assert budget.snapshot() == {'normal': 10, 'campaign': 9, 'smoke': 1, 'total': 20}


def test_unknown_transport_outcome_uses_the_smoke_slot_and_cannot_retry(tmp_path):
    budget = ImageBudget.create(tmp_path / 'timeout.sqlite')
    journal = Journal()
    seen = []

    async def provider(request):
        assert budget.snapshot()['smoke'] == 1
        seen.append(request.method)
        raise httpx.ReadTimeout('server may have received the request', request=request)

    async def run():
        url = 'https://api.soonstudio.ai/v1/images/generations?async=true'
        body = {'n': 1, 'model': MODEL, 'prompt': 'single image', 'response_format': 'url'}
        boundary = SoonPostBudget(httpx.MockTransport(provider), budget=budget, scenario='smoke', journal=journal)
        async with httpx.AsyncClient(transport=boundary, trust_env=False) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post(url, json=body)
            with pytest.raises(SafetyViolation, match='repeat_post'):
                await client.post(url, json=body)
        new_boundary = SoonPostBudget(httpx.MockTransport(provider), budget=budget, scenario='smoke', journal=journal)
        async with httpx.AsyncClient(transport=new_boundary, trust_env=False) as client:
            with pytest.raises(SoonBudgetExhausted):
                await client.post(url, json=body)

    asyncio.run(run())
    assert seen == ['POST']
    assert budget.snapshot() == {'normal': 0, 'campaign': 0, 'smoke': 1, 'total': 1}
    assert journal.rows[1]['status'] == 'unknown'
    assert journal.rows[-1]['reason'] == 'budget_exhausted'


@pytest.mark.parametrize('method,url,rewrite', [
    ('POST', 'http://api.soonstudio.ai/v1/images/generations?async=true', None),
    ('POST', 'https://wrong.invalid/v1/images/generations?async=true', None),
    ('POST', 'https://api.soonstudio.ai/v1/images/generations?async=true&n=2', None),
    ('GET', 'https://api.soonstudio.ai/v1/images/generations?async=true', None),
    ('POST', None, '"n":2'),
    ('POST', None, '"n":true'),
    ('POST', None, '"n":"1"'),
    ('POST', None, '"n":2,"n":1'),
    ('POST', None, '"n":1,"extra_body":{"n":2}'),
])
def test_only_exact_single_image_post_reaches_transport(tmp_path, method, url, rewrite):
    budget = ImageBudget.create(tmp_path / 'invalid.sqlite')
    journal = Journal()

    async def provider(request):
        pytest.fail('invalid request reached transport')

    async def run():
        body = '{"model":"' + MODEL + '","prompt":"one","response_format":"url",' + (rewrite or '"n":1') + '}'
        guarded = SoonPostBudget(httpx.MockTransport(provider), budget=budget, scenario='normal', journal=journal)
        async with httpx.AsyncClient(transport=guarded, trust_env=False) as client:
            with pytest.raises(SafetyViolation):
                await client.request(method, url or 'https://api.soonstudio.ai/v1/images/generations?async=true',
                                     content=body, headers={'content-type': 'application/json'})

    asyncio.run(run())
    assert budget.snapshot() == {'normal': 0, 'campaign': 0, 'smoke': 0, 'total': 0}
    assert [row['kind'] for row in journal.rows] == ['soon_submit_blocked']
