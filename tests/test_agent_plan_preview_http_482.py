"""Public HTTP boundaries for #482, without replacing scope, edit, CAS or approval."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.test_agent_plan_399 import env, submit, tool, upload, workbook


ORIGIN = 'http://127.0.0.1:3198'


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['detail']['code'] == code, response.text


class HumanPreviewHTTP:
    def __init__(self, client, project, foreign):
        self.client, self.project, self.foreign = client, project, foreign
        source = upload(client, project, 'http-boundaries.xlsx', workbook(''))
        submit(client, project, source)
        self.draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']}, mcp=True)
        assert self.draft['generation'] == 1, self.draft
        self.before = self.state()

    def headers(self, **overrides):
        return {'Origin': ORIGIN, 'X-Agent-Project': self.project, **overrides}

    def body(self, *, generation=None, name='HTTP boundary edit'):
        return {'generation': self.draft['generation'] if generation is None else generation,
                'ops': [{'task_id': self.draft['preview']['tasks'][0]['task_id'], 'rename': name}]}

    def request(self, method, *, project=None, plan_id=None, params=None, headers=None, **kwargs):
        project = self.project if project is None else project
        plan_id = self.draft['plan_id'] if plan_id is None else plan_id
        path = f'/api/projects/{project}/plan/previews/{plan_id}'
        if method == 'POST':
            path += '/edits'
            if not {'json', 'content'} & kwargs.keys():
                kwargs['json'] = self.body()
        return self.client.request(
            method, path, params={'agent_node_id': 'actor'} if params is None else params,
            headers={'Origin': ORIGIN, 'X-Agent-Project': project} if headers is None else headers,
            **kwargs,
        )

    def state(self):
        result = {}
        for project in (self.project, self.foreign):
            opened = self.client.get(f'/api/project-library/{project}')
            media = self.client.get(f'/api/project-library/{project}/media')
            assert opened.status_code == 200, opened.text
            assert media.status_code == 200, media.text
            result[project] = {'canvas': opened.json()['canvas'], 'media': media.json()}
        return result

    def assert_readback_and_no_writes(self, expected=None):
        current = self.request('GET')
        assert current.status_code == 200, current.text
        assert current.json() == (self.draft if expected is None else expected)
        assert self.state() == self.before

    def setup_nodes(self, mutations):
        revision = self.state()[self.project]['canvas']['revision']
        response = self.client.patch(f'/api/project-library/{self.project}/canvas', headers=self.headers(),
                                     json={'base_revision': revision, 'mutations': mutations})
        assert response.status_code == 200, response.text
        self.before = self.state()


@pytest.fixture
def http_preview(env):
    client, _, _, (project, foreign) = env
    return HumanPreviewHTTP(client, project, foreign)


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('headers', [
    pytest.param({}, id='no-origin-or-referer'),
    pytest.param({'Referer': 'http://untrusted.invalid/page'}, id='foreign-referer'),
    pytest.param({'Origin': 'http://untrusted.invalid'}, id='foreign-origin'),
    pytest.param({'Origin': 'http://127.0.0.1:3197'}, id='wrong-port'),
    pytest.param({'Origin': 'https://127.0.0.1:3198'}, id='wrong-scheme'),
    pytest.param({'Origin': 'http://localhost:3198'}, id='different-loopback-origin'),
    pytest.param({'Origin': 'null', 'Referer': ORIGIN + '/canvas'}, id='opaque-origin-with-good-referer'),
    pytest.param({'Origin': 'file:///outside.html'}, id='file-origin'),
    pytest.param({'Origin': 'http://untrusted.invalid', 'Referer': ORIGIN + '/canvas'},
                 id='foreign-origin-with-good-referer'),
    pytest.param({'Host': 'untrusted.invalid:3198', 'Origin': 'http://untrusted.invalid:3198'},
                 id='fake-host-even-with-matching-origin'),
    pytest.param({'Host': '127.0.0.1:3197', 'Origin': ORIGIN}, id='host-origin-port-mismatch'),
])
def test_strict_same_origin_is_required_for_both_methods(http_preview, method, headers):
    http = http_preview
    response = http.request(method, headers={'X-Agent-Project': http.project, **headers})
    assert response.status_code == 403, response.text
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_same_origin_referer_without_origin_is_accepted(http_preview, method):
    http = http_preview
    response = http.request(method, headers={'Referer': ORIGIN + '/canvas?project=' + http.project,
                                            'X-Agent-Project': http.project})
    assert response.status_code == 200, response.text
    expected = response.json()
    if method == 'GET':
        assert expected == http.draft
    else:
        assert expected['generation'] == 2
        assert expected['preview']['tasks'][0]['name'] == 'HTTP boundary edit'
    http.assert_readback_and_no_writes(expected)


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('header_case', ['missing', 'wrong'])
def test_project_header_is_required_and_must_match(http_preview, method, header_case):
    http = http_preview
    headers = {'Origin': ORIGIN}
    if header_case == 'wrong':
        headers['X-Agent-Project'] = http.foreign
    response = http.request(method, headers=headers)
    assert_error(response, 400 if header_case == 'missing' else 403,
                 'INVALID_ARGUMENT' if header_case == 'missing' else 'PROJECT_MISMATCH')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('field', ['unexpected', 'caller', 'binding', 'source'])
def test_unknown_edit_json_fields_are_rejected(http_preview, field):
    http = http_preview
    response = http.request('POST', json={**http.body(), field: 'human'})
    assert_error(response, 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('content', [
    pytest.param('', id='empty'),
    pytest.param('{"generation":1,"ops":[', id='truncated'),
    pytest.param('{"generation":1,"ops":[],}', id='trailing-comma'),
    pytest.param('null', id='null-object'),
    pytest.param('[]', id='array-object'),
    pytest.param('"not an object"', id='string-object'),
])
def test_bad_json_or_non_object_edit_body_is_rejected(http_preview, content):
    http = http_preview
    response = http.request('POST', content=content, headers=http.headers(**{'Content-Type': 'application/json'}))
    assert_error(response, 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('generation', [
    pytest.param(True, id='true'), pytest.param(False, id='false'),
    pytest.param(1.0, id='float-one'), pytest.param(1.5, id='fraction'),
    pytest.param(0, id='zero'), pytest.param(-1, id='negative'),
    pytest.param(None, id='null'), pytest.param('1', id='string'),
])
def test_edit_generation_must_be_a_positive_json_integer(http_preview, generation):
    http = http_preview
    response = http.request('POST', json={**http.body(), 'generation': generation})
    assert_error(response, 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('field', ['generation', 'ops'])
def test_required_edit_fields_cannot_be_omitted(http_preview, field):
    http = http_preview
    body = http.body()
    del body[field]
    assert_error(http.request('POST', json=body), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('generation', ['', 'true', '1.0', '0', '-1', 'not-a-number'])
def test_bad_get_generation_is_invalid_not_stale(http_preview, generation):
    http = http_preview
    response = http.request('GET', params={'agent_node_id': 'actor', 'generation': generation})
    assert_error(response, 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_positive_but_unknown_generation_is_stale(http_preview, method):
    http = http_preview
    if method == 'GET':
        response = http.request(method, params={'agent_node_id': 'actor', 'generation': 2})
    else:
        response = http.request(method, json=http.body(generation=2))
    assert_error(response, 409, 'PLAN_STALE')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('params', [
    pytest.param([('agent_node_id', 'actor'), ('unexpected', 'x')], id='unknown-field'),
    pytest.param([('agent_node_id', 'actor'), ('agent_node_id', 'actor')], id='duplicate-identical-actor'),
    pytest.param([('agent_node_id', 'actor'), ('agent_node_id', 'other')], id='duplicate-different-actor'),
    pytest.param([], id='missing-actor'),
    pytest.param({'agent_node_id': ''}, id='empty-actor'),
    pytest.param({'agent_node_id': '   '}, id='blank-actor'),
    pytest.param({'agent_node_id': 'a' * 129}, id='overlong-actor'),
])
def test_unknown_repeated_or_invalid_actor_query_is_rejected(http_preview, method, params):
    http = http_preview
    assert_error(http.request(method, params=params), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_generation_query_cannot_be_repeated_or_substitute_for_post_json(http_preview, method):
    http = http_preview
    params = [('agent_node_id', 'actor'), ('generation', '1')]
    if method == 'GET':
        params.append(('generation', '1'))
    assert_error(http.request(method, params=params), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('scope_case', ['foreign-project', 'other-agent', 'missing-agent', 'unknown-plan'])
def test_preview_identity_cannot_cross_project_or_actor(http_preview, method, scope_case):
    http = http_preview
    kwargs = {}
    if scope_case == 'foreign-project':
        kwargs['project'] = http.foreign
    elif scope_case in ('other-agent', 'missing-agent'):
        kwargs['params'] = {'agent_node_id': 'other' if scope_case == 'other-agent' else 'missing'}
    else:
        kwargs['plan_id'] = '0' * 32
    assert_error(http.request(method, **kwargs), 409, 'PLAN_STALE')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('replacement', ['deleted', 'non-agent'])
def test_deleted_or_non_agent_importer_cannot_read_or_edit_its_preview(http_preview, method, replacement):
    http = http_preview
    http.assert_readback_and_no_writes()
    mutations = [{'op': 'remove_node', 'node_id': 'actor'}]
    if replacement == 'non-agent':
        # Keep the cached project/actor identity valid so this exercises the node-type guard.
        mutations.append({'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-prompt', 'text': 'human'}})
    http.setup_nodes(mutations)
    assert_error(http.request(method), 409, 'PLAN_STALE')
    assert_error(http.request('GET'), 409, 'PLAN_STALE')
    assert http.state() == http.before


@pytest.mark.parametrize('ops', [
    pytest.param(None, id='null'), pytest.param({}, id='object'),
    pytest.param('rename', id='string'), pytest.param([], id='empty'),
    pytest.param([None], id='null-op'), pytest.param([[]], id='array-op'),
    pytest.param([{}], id='missing-task'),
])
def test_invalid_ops_container_and_non_object_ops_are_rejected(http_preview, ops):
    http = http_preview
    assert_error(http.request('POST', json={**http.body(), 'ops': ops}), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


def test_ops_above_the_200_edit_limit_are_rejected_without_partial_changes(http_preview):
    http = http_preview
    body = http.body()
    body['ops'] *= 201
    assert_error(http.request('POST', json=body), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


@pytest.mark.parametrize('bad_op', [
    pytest.param({'split': 4}, id='split'),
    pytest.param({'set_refs': []}, id='set-refs'),
    pytest.param({'rename': 'two actions', 'select': True}, id='multiple-actions'),
    pytest.param({}, id='no-action'),
    pytest.param({'select': 1}, id='non-boolean-select'),
    pytest.param({'rename': '   '}, id='blank-rename'),
    pytest.param({'set_section': None}, id='null-section'),
    pytest.param({'merge_into': '$self'}, id='self-merge'),
    pytest.param({'task_id': None, 'rename': 'invalid id'}, id='invalid-task-id'),
])
def test_invalid_later_op_atomically_rolls_back_a_valid_first_op(http_preview, bad_op):
    http = http_preview
    first = http.draft['preview']['tasks'][0]['task_id']
    op = {'task_id': first, **bad_op}
    if op.get('merge_into') == '$self':
        op['merge_into'] = first
    body = http.body()
    body['ops'].append(op)
    assert_error(http.request('POST', json=body), 400, 'INVALID_ARGUMENT')
    http.assert_readback_and_no_writes()


def test_missing_task_in_later_op_is_atomic_and_does_not_consume_generation(http_preview):
    http = http_preview
    body = http.body()
    body['ops'].append({'task_id': 'missing-task', 'rename': 'not applied'})
    assert_error(http.request('POST', json=body), 404, 'TASK_NOT_FOUND')
    http.assert_readback_and_no_writes()
    accepted = http.request('POST')
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['generation'] == 2
    http.assert_readback_and_no_writes(accepted.json())


def test_all_four_supported_actions_are_visible_on_public_readback(http_preview):
    http = http_preview
    tasks = http.draft['preview']['tasks']
    first, second, third = [task['task_id'] for task in tasks]
    response = http.request('POST', json={'generation': 1, 'ops': [
        {'task_id': first, 'select': True},
        {'task_id': first, 'rename': '  Approved   title  '},
        {'task_id': third, 'set_section': '  Human   section  '},
        {'task_id': second, 'merge_into': first},
    ]})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['plan_id'] == http.draft['plan_id'] and edited['generation'] == 2
    by_id = {task['task_id']: task for task in edited['preview']['tasks']}
    assert set(by_id) == {first, third}
    assert by_id[first]['selected'] is True and by_id[first]['name'] == 'Approved title'
    assert by_id[third]['section'] == 'Human section'
    assert by_id[first]['source_rows'] == ['图片方案!3', '图片方案!5']
    assert '白底拍摄' in by_id[first]['base_prompt'] and '场景共4张图' in by_id[first]['base_prompt']
    assert edited['preview']['sections'] == {'主图方案': 1, 'Human section': 1}
    assert all(task['refs'] == [] for task in by_id.values())
    http.assert_readback_and_no_writes(edited)
    assert http.draft['generation'] == 1 and len(http.draft['preview']['tasks']) == 3


@pytest.mark.parametrize('other_binding', ['rest', 'different-mcp-session'])
def test_human_edit_preserves_agent_exact_scope_and_shared_generation(http_preview, other_binding):
    http = http_preview
    saved = http.request('POST')
    assert saved.status_code == 200, saved.text
    args = {'plan_id': http.draft['plan_id'], **http.body(generation=2, name='Agent continuation')}
    kwargs = {'mcp': False} if other_binding == 'rest' else {'mcp': True, 'sid': 'unrelated-http-boundary'}
    rejected = tool(http.client, http.project, 'plan.edit_preview', args, **kwargs)
    assert rejected['code'] == 'PLAN_STALE', rejected
    http.assert_readback_and_no_writes(saved.json())
    continued = tool(http.client, http.project, 'plan.edit_preview', args, mcp=True)
    assert continued['generation'] == 3, continued
    assert continued['preview']['tasks'][0]['name'] == 'Agent continuation'
    http.assert_readback_and_no_writes(continued)


def test_parallel_http_edits_with_one_generation_have_exactly_one_winner(http_preview):
    http = http_preview
    barrier = Barrier(2)

    def edit(name):
        barrier.wait(timeout=10)
        return http.request('POST', json=http.body(name=name))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, name) for name in ('Concurrent A', 'Concurrent B')]
        responses = [future.result(timeout=30) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    winner = next(response.json() for response in responses if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 409)
    assert_error(loser, 409, 'PLAN_STALE')
    assert winner['generation'] == 2
    assert winner['preview']['tasks'][0]['name'] in ('Concurrent A', 'Concurrent B')
    http.assert_readback_and_no_writes(winner)


@pytest.mark.parametrize('activity', ['GET', 'POST'])
def test_original_1800_second_ttl_is_not_refreshed_by_read_or_edit(env, monkeypatch, activity):
    clock = [10000.0]
    monkeypatch.setattr('app.services.agent_plan.monotonic', lambda: clock[0])
    client, _, _, (project, foreign) = env
    http = HumanPreviewHTTP(client, project, foreign)
    clock[0] += 1799
    response = http.request(activity)
    assert response.status_code == 200, response.text
    latest = response.json()
    assert latest['generation'] == (1 if activity == 'GET' else 2)
    clock[0] += 0.999
    http.assert_readback_and_no_writes(latest)
    clock[0] = 11800.0
    assert_error(http.request('GET', params={'agent_node_id': 'actor', 'generation': latest['generation']}),
                 409, 'PLAN_STALE')
    assert_error(http.request('GET'), 409, 'PLAN_STALE')
    assert_error(http.request('POST', json=http.body(generation=latest['generation'])), 409, 'PLAN_STALE')
    assert http.state() == http.before
