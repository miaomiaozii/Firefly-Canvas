"""Controlled #493 final-review regressions against ASGI, ProjectStore and Agent journal."""
import asyncio
import base64
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import main
from app import core
from app.routers import api_agent_tools as agents
from app.routers import api_project_library as api
from app.services import providers, result_downloads as rd
from app.services.agent_ops_context import agent_ops_scope
from firefly_projects import InvalidMutation, ProjectNotFound, ProjectStoreError
from tests.test_result_downloads_493 import fixture, network, png, Body, PROVIDER, URL
from tests.test_result_downloads_d2_493 import client


def route(model='fixture-model'):
    return SimpleNamespace(provider_id=PROVIDER['id'], provider_name='Fixture', model=model,
        reason='manual', to_dict=lambda: {'provider_id': PROVIDER['id'], 'model': model})


def inline(content):
    return {'type': 'b64', 'value': base64.b64encode(content).decode('ascii')}


@pytest.mark.parametrize('layout', ['url', 'url-inline', 'inline-url'])
def test_agent_n2_journal_yield_preserves_every_paid_candidate(fixture, layout):
    f = fixture
    contents = [png(), png(True)]
    network(f, lambda req: httpx.Response(200, stream=Body(contents[int(req.url.params['candidate'])])))
    f.monkeypatch.setattr(main, 'route_image_generation', lambda *a, **k: route())
    f.monkeypatch.setattr(main, 'load_api_providers', lambda **k: [{**PROVIDER, 'image_models': ['fixture-model']}])
    canvas = f.store.canvas(f.project)
    f.store.apply_mutations(f.project, [{'op': 'upsert_node', 'node': {'id': 'source', 'type': 'prompt',
        'text': 'controlled fixture', 'runSettings': {'provider_id': PROVIDER['id'], 'model': 'fixture-model', 'count': 2}}}],
        base_revision=canvas['revision'])
    generated = []
    checkpoints = []
    real_checkpoint = f.queue.checkpoint
    async def checkpoint(*args, **kwargs):
        ids = await real_checkpoint(*args, **kwargs)
        checkpoints.append(copy.deepcopy(f.queue.load(args[1]['id'])))
        return ids
    f.monkeypatch.setattr(f.queue, 'checkpoint', checkpoint)
    async def generate(*args, **kwargs):
        n = len(generated)
        remote = {'type': 'url', 'value': URL + '&candidate=' + str(n)}
        embedded = inline(contents[1-n])
        candidates = [remote] if layout == 'url' else ([remote, embedded] if layout == 'url-inline' else [embedded, remote])
        generated.append(candidates)
        raw = {'task_id': 'paid-original-' + str(n), 'data': [
            {'url': c['value']} if c['type'] == 'url' else {'b64_json': c['value']} for c in candidates]}
        return candidates[0], raw
    f.monkeypatch.setattr(api, 'run_routed_image_candidate', generate)
    original_effect = agents._AgentImageLifecycle.effect
    original_remember = f.queue.remember_saved
    async def run():
        second_checkpointed, first_saved = asyncio.Event(), asyncio.Event()
        archive_effects = 0
        async def effect(self, description):
            nonlocal archive_effects
            await original_effect(self, description)
            if description == 'generated media archive attempted':
                archive_effects += 1
                if archive_effects == 1:
                    await asyncio.wait_for(second_checkpointed.wait(), 10)
                elif archive_effects == 2:
                    second_checkpointed.set()
                    await asyncio.wait_for(first_saved.wait(), 10)
        def remember(*args, **kwargs):
            result = original_remember(*args, **kwargs)
            first_saved.set()
            return result
        f.monkeypatch.setattr(agents._AgentImageLifecycle, 'effect', effect)
        f.monkeypatch.setattr(f.queue, 'remember_saved', remember)
        with agent_ops_scope(project_id=f.project, agent_node_id='actor', turn_id='final-review-turn',
                             tool='generate.trigger', args={'node_id': 'source'}):
            started = await agents.trigger_generation_core(f.project, 'source', agent_node_id='actor')
        task_id = started['task_id']
        try:
            await asyncio.wait_for(core.PROJECT_IMAGE_GENERATION_FUTURES[task_id], 20)
            task = core.CANVAS_TASKS[task_id]
            assert task['status'] == 'succeeded'
            canvas = f.store.canvas(f.project)
            history = next(h for h in canvas['history'] if h.get('task_id') == task_id)
            node = next(n for n in canvas['nodes'] if n['id'] == started['result_node_id'])
            expected = 2 if layout == 'url' else 4
            assert len(f.requests) == 2, 'Both paid URL candidates must actually be fetched'
            assert len(generated) == 2
            assert len(history['outputs']) == len(node['images']) == expected
            ids = [o['output_id'] for o in history['outputs']]
            assert len(set(ids)) == expected
            assert set(ids) == {o['output_id'] for o in node['images']}
            slots = checkpoints[-1]['items']
            assert set(ids) == {i['output_id'] for i in slots}, 'Inline and URL results must retain their checkpoint occurrence identity'
            actual = [hashlib.sha256(Path(f.store.media_record(f.project, o['asset_id'])['path']).read_bytes()).hexdigest()
                      for o in history['outputs']]
            expected_hashes = [hashlib.sha256(b).hexdigest() for b in contents]
            assert sorted(actual) == sorted(expected_hashes * (1 if layout == 'url' else 2))
            by_id = {o['output_id']: o for o in history['outputs']}
            node_by_id = {o['output_id']: o for o in node['images']}
            for slot in slots:
                candidate_number = int(slot['upstream_task_id'].rsplit('-', 1)[1])
                candidate = generated[candidate_number][slot['upstream_output_index']]
                content = contents[candidate_number] if candidate['type'] == 'url' else contents[1-candidate_number]
                output = by_id[slot['output_id']]
                media = f.store.media_record(f.project, output['asset_id'])
                assert Path(media['path']).read_bytes() == content
                assert output['asset_id'] == node_by_id[slot['output_id']]['asset_id']
                assert output['output_order'] == slot['index']
            assert not list((f.store.root / f.project / 'private-result-downloads').glob('*.json'))
        finally:
            core.CANVAS_TASKS.pop(task_id, None)
    asyncio.run(run())


def test_deleted_history_does_not_hide_valid_retrieval_groups(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Listing must never fetch media'))
    async def run():
        first = {'type': 'url', 'value': URL}
        await f.queue.checkpoint(f.project, f.history, [first], {'task_id': 'deleted-original'}, PROVIDER)
        other = {**f.history, 'id': 'generation-' + uuid.uuid4().hex, 'task_id': 'second-local'}
        f.store.begin_image_generation(f.project, 'result', {'taskId': other['task_id'], 'count': 1},
                                      base_revision=f.store.canvas(f.project)['revision'])
        await f.queue.checkpoint(f.project, other, [{'type': 'url', 'value': URL + '&second=1'}],
                                 {'task_id': 'valid-original'}, PROVIDER)
        f.store.delete_history_record(f.project, f.history['id'], base_revision=f.store.canvas(f.project)['revision'])
        deleted_path = f.store.root / f.project / 'private-result-downloads' / (f.history['id'] + '.json')
        deleted_bytes = deleted_path.read_bytes()
        before = copy.deepcopy(f.store.canvas(f.project))
        async with client() as c:
            response = await c.get(f'/api/project-library/{f.project}/image-result-downloads')
            assert response.status_code == 200, response.text
            groups = response.json()['groups']
            valid = next(g for g in groups if g['history_id'] == other['id'])
            assert valid['items'][0]['can_retrieve'] is True
            invalid = [g for g in groups if g['history_id'] == f.history['id']]
            assert not invalid or invalid[0]['status'] == 'unavailable'
            missing = await c.get(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}')
            assert missing.status_code == 404
        assert f.store.canvas(f.project) == before
        assert deleted_path.read_bytes() == deleted_bytes
        assert not f.requests
    asyncio.run(run())


def test_resuming_task_does_not_duplicate_successful_sibling_occurrence(fixture):
    f = fixture
    network(f, lambda req: httpx.Response(200, stream=Body(png(True))))
    async def run():
        f.store.begin_image_generation(f.project, 'result', {'taskId': 'sibling-A', 'count': 1},
                                      base_revision=f.store.canvas(f.project)['revision'])
        await f.queue.checkpoint(f.project, f.history, [{'type': 'url', 'value': URL}],
                                 {'task_id': 'paid-B'}, PROVIDER)
        saved = await api.archive_project_generated_image(f.project, inline(png()), 0)
        saved['output_id'] = 'output-' + uuid.uuid4().hex
        sibling = {**f.history, 'id': 'generation-' + uuid.uuid4().hex, 'task_id': 'sibling-A',
                   'status': 'succeeded', 'outputs': [saved]}
        f.store.complete_image_generation(f.project, 'result', [saved], sibling,
                                         task_id='sibling-A', require_pending=True)
        before = copy.deepcopy(f.store.canvas(f.project))
        assert before['nodes'][0]['_generationBatchApplied'] is True
        expected_b = f.queue.load(f.history['id'])['items'][0]['output_id']
        async with client() as c:
            response = await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert response.status_code == 200 and response.json()['status'] == 'succeeded', response.text
        f.store.flush(f.project)
        canvas = f.store.canvas(f.project)
        node = canvas['nodes'][0]
        assert [o['output_id'] for o in node['images']] == [saved['output_id'], expected_b]
        assert node['images'][0] == before['nodes'][0]['images'][0]
        assert next(h for h in canvas['history'] if h['id'] == sibling['id']) == next(
            h for h in before['history'] if h['id'] == sibling['id'])
        assert [o['output_id'] for o in next(h for h in canvas['history'] if h['id'] == f.history['id'])['outputs']] == [expected_b]
        assert len(f.requests) == 1
    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['openai', 'gemini'])
@pytest.mark.parametrize('legacy', [False, True])
def test_refresh_reuses_selected_model_endpoint_and_auth_after_restart(fixture, protocol, legacy):
    f = fixture
    model = 'Exact-Selected-Model-493'
    provider = {**PROVIDER, 'image_request_mode': 'openai-video-proxy',
                'model_request_modes': {model: 'openai-json'}, 'model_protocols': {model: protocol}}
    f.monkeypatch.setattr(main, 'get_api_provider_required', lambda *a, **k: copy.deepcopy(provider))
    f.monkeypatch.setattr(main, 'provider_env_key_value', lambda *a: 'offline-synthetic-493')
    f.monkeypatch.setattr(main, 'api_headers', providers.api_headers)
    f.history.update(actual_model=model, model='Historical-Alias', requested_model='Not-The-Selected-Model')
    def respond(req):
        if req.url.path == '/v1/images/tasks/paid-original-model':
            if protocol == 'gemini':
                assert 'x-goog-api-key' in req.headers and 'authorization' not in req.headers
            else:
                assert 'authorization' in req.headers and 'x-goog-api-key' not in req.headers
            return httpx.Response(200, json={'data': [{'url': URL + '&refreshed=1', 'expires_in': 600}]})
        if '/videos/' in req.url.path:
            return httpx.Response(404)
        assert 'authorization' not in req.headers and 'x-goog-api-key' not in req.headers
        return httpx.Response(200, stream=Body(png()))
    network(f, respond)
    async def run():
        candidate = {'type': 'url', 'value': URL, 'expires_at': time.time()-1}
        await f.queue.checkpoint(f.project, f.history, [candidate], {'task_id': 'paid-original-model'}, provider)
        queue = f.queue.load(f.history['id'])
        assert queue['model'] == model
        if legacy:
            queue.pop('model')
            queue.pop('model_identity')
            f.queue.save(queue)
        restarted = rd.ResultDownloads(f.queue.root, main)
        f.monkeypatch.setitem(rd._instances, str(f.store.root), restarted)
        async with client() as c:
            response = await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert response.status_code == 200
        queries = [req.url.path for req in f.requests if '/tasks/' in req.url.path or '/videos/' in req.url.path]
        assert queries == ['/v1/images/tasks/paid-original-model']
        assert response.json()['status'] == 'succeeded'
        assert len(f.requests) == 2 and all(req.method == 'GET' for req in f.requests)
        assert f.store.canvas(f.project)['history'][0]['status'] == 'succeeded'
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['load', 'validate', 'path'])
@pytest.mark.parametrize('error', [ProjectStoreError('corruption'), OSError('IO'), ValueError('invalid queue')])
def test_listing_does_not_swallow_corruption_or_io(fixture, stage, error):
    f = fixture
    network(f, lambda req: pytest.fail('Listing must never fetch media'))
    async def run():
        await f.queue.checkpoint(f.project, f.history, [{'type': 'url', 'value': URL}], {}, PROVIDER)
        def fail(*a, **kw):
            raise error
        if stage == 'path':
            f.monkeypatch.setattr(f.store, 'image_result_download_path', fail)
        else:
            f.monkeypatch.setattr(f.queue, stage, fail)
        async with client() as c:
            with pytest.raises(type(error), match=str(error)):
                await c.get(f'/api/project-library/{f.project}/image-result-downloads')
        assert not f.requests
    asyncio.run(run())


def test_listing_surfaces_malformed_private_queue(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Listing must never fetch media'))
    async def run():
        await f.queue.checkpoint(f.project, f.history, [{'type': 'url', 'value': URL}], {}, PROVIDER)
        path = Path(f.store.image_result_download_queue_path(f.project, f.history['id']))
        path.write_text('{', encoding='utf-8')
        async with client() as c:
            with pytest.raises(json.JSONDecodeError):
                await c.get(f'/api/project-library/{f.project}/image-result-downloads')
        assert path.read_text(encoding='utf-8') == '{'
        assert not f.requests
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['validate', 'path'])
@pytest.mark.parametrize('error_type', [ProjectNotFound, InvalidMutation])
def test_listing_skips_only_group_ownership_failures(fixture, stage, error_type):
    f = fixture
    network(f, lambda req: pytest.fail('Listing must never fetch media'))
    async def run():
        await f.queue.checkpoint(f.project, f.history, [{'type': 'url', 'value': URL}], {}, PROVIDER)
        def fail(*a, **kw):
            raise error_type('orphan')
        target = f.store if stage == 'path' else f.queue
        f.monkeypatch.setattr(target, 'image_result_download_path' if stage == 'path' else stage, fail)
        async with client() as c:
            response = await c.get(f'/api/project-library/{f.project}/image-result-downloads')
            assert response.status_code == 200
            assert response.json() == {'groups': []}
        assert not f.requests
    asyncio.run(run())


@pytest.mark.parametrize('changed', ['mode', 'protocol'])
def test_refresh_rejects_changed_selected_model_route_before_any_get(fixture, changed):
    f = fixture
    model = 'Exact-Selected-Model-493'
    provider = {**PROVIDER, 'image_request_mode': 'openai-video-proxy',
                'model_request_modes': {model: 'openai-json'}, 'model_protocols': {model: 'openai'}}
    f.history.update(actual_model=model)
    f.monkeypatch.setattr(main, 'get_api_provider_required', lambda *a, **k: copy.deepcopy(provider))
    f.monkeypatch.setattr(main, 'provider_env_key_value', lambda *a: 'offline-synthetic-493')
    f.monkeypatch.setattr(main, 'api_headers', providers.api_headers)
    network(f, lambda req: pytest.fail('Changed selected-model identity must never query another route'))
    async def run():
        await f.queue.checkpoint(f.project, f.history, [{'type': 'url', 'value': URL, 'expires_at': time.time()-1}],
                                 {'task_id': 'paid-original-model'}, provider)
        provider['model_request_modes' if changed == 'mode' else 'model_protocols'][model] = (
            'openai-video-proxy' if changed == 'mode' else 'gemini')
        result = await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link)
        assert result['items'][0]['status'] == 'unavailable'
        assert not f.requests
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['checkpoint', 'refresh'])
def test_unknown_original_model_never_falls_back_to_platform_route(fixture, phase):
    f = fixture
    network(f, lambda req: pytest.fail('Unknown original model must never query a guessed task API'))
    async def run():
        candidate = {'type': 'url', 'value': URL, 'expires_at': time.time()-1}
        if phase == 'checkpoint':
            history = {k: v for k, v in f.history.items() if k not in {'actual_model', 'model', 'route'}}
            history['requested_model'] = 'Not-The-Original-Model'
            before = copy.deepcopy(f.store.canvas(f.project))
            with pytest.raises(InvalidMutation):
                await f.queue.checkpoint(f.project, history, [candidate], {'task_id': 'paid-unknown-model'}, PROVIDER)
            assert f.store.canvas(f.project) == before
            assert not Path(f.store.image_result_download_queue_path(f.project, f.history['id'])).exists()
        else:
            await f.queue.checkpoint(f.project, f.history, [candidate], {'task_id': 'paid-unknown-model'}, PROVIDER)
            queue = f.queue.load(f.history['id'])
            queue.pop('model')
            queue.pop('model_identity')
            queue['history'] = {k: v for k, v in queue['history'].items() if k not in {'actual_model', 'model', 'route'}}
            queue['history']['requested_model'] = 'Not-The-Original-Model'
            f.queue.save(queue)
            result = await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link)
            assert result['items'][0]['status'] == 'unavailable'
        assert not f.requests
    asyncio.run(run())
