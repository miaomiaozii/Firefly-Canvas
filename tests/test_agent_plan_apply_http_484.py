"""The browser endpoint accepts only same-origin, exact-project human intent (#484)."""
from copy import deepcopy

import pytest

from tests.test_agent_plan_399 import env
from tests.test_agent_plan_apply_484 import ApplyHTTP, ORIGIN, assert_human_apply_error, headers
from app.services import agent_plan


@pytest.fixture
def http(env):
    result = ApplyHTTP(env, mcp=True)
    result.select(0)
    return result


def unchanged(http, before, draft):
    assert http.state() == before
    assert http.read() == draft
    assert http.events.empty() and not http.session.confirm_handlers


@pytest.mark.parametrize('origin_headers', [
    pytest.param({}, id='absent-origin-and-referer'),
    pytest.param({'Origin': 'http://untrusted.invalid'}, id='foreign-origin'),
    pytest.param({'Referer': 'http://untrusted.invalid/canvas'}, id='foreign-referer'),
    pytest.param({'Origin': 'http://127.0.0.1:3197'}, id='wrong-port'),
    pytest.param({'Origin': 'https://127.0.0.1:3198'}, id='wrong-scheme'),
    pytest.param({'Origin': 'http://localhost:3198'}, id='different-loopback-origin'),
    pytest.param({'Origin': 'null', 'Referer': ORIGIN + '/canvas'}, id='opaque-origin'),
    pytest.param({'Origin': 'file:///outside.html'}, id='file-origin'),
    pytest.param({'Origin': 'http://evil.invalid', 'Referer': ORIGIN + '/canvas'}, id='referer-cannot-override-origin'),
    pytest.param({'Host': 'evil.invalid:3198', 'Origin': 'http://evil.invalid:3198'}, id='spoofed-host'),
    pytest.param({'Host': '127.0.0.1:3197', 'Origin': ORIGIN}, id='host-port-mismatch'),
])
def test_human_apply_requires_strict_same_origin_without_opening_confirmation(http, origin_headers):
    before, draft = http.state(), http.read()
    response = http.apply(headers={'X-Agent-Project': http.project, **origin_headers})
    assert response.status_code == 403, response.text
    unchanged(http, before, draft)


def test_same_origin_referer_is_valid_but_never_itself_approves(http):
    before = http.state()
    # Empty selection is a deterministic validation result after origin admission.
    http.edit({'task_id': http.draft['preview']['tasks'][0]['task_id'], 'select': False})
    response = http.apply(headers={'Referer': ORIGIN + '/canvas', 'X-Agent-Project': http.project})
    assert_human_apply_error(response, 400, 'INVALID_ARGUMENT')
    assert http.state() == before and http.events.empty()


@pytest.mark.parametrize('project_header', ['missing', 'foreign', 'whitespace'])
def test_project_header_must_exactly_match_not_merely_be_present(http, project_header):
    before, draft = http.state(), http.read()
    request_headers = {'Origin': ORIGIN}
    if project_header != 'missing':
        request_headers['X-Agent-Project'] = http.foreign if project_header == 'foreign' else ' ' + http.project
    assert_human_apply_error(http.apply(headers=request_headers), 400 if project_header == 'missing' else 403,
                 'INVALID_ARGUMENT' if project_header == 'missing' else 'PROJECT_MISMATCH')
    unchanged(http, before, draft)


@pytest.mark.parametrize('same_value', [True, False], ids=['duplicate-matching', 'duplicate-conflicting'])
def test_repeated_project_header_is_not_an_exact_project_identity(http, same_value):
    before, draft = http.state(), http.read()
    request_headers = [('Origin', ORIGIN), ('X-Agent-Project', http.project),
                       ('X-Agent-Project', http.project if same_value else http.foreign)]
    assert_human_apply_error(http.apply(headers=request_headers), 403, 'PROJECT_MISMATCH')
    unchanged(http, before, draft)


@pytest.mark.parametrize('field', ['task_ids', 'layout', 'source', 'binding', 'caller', 'approved', 'turn_id',
                                    'apply_via', 'agent_node_id', 'unexpected'])
def test_browser_cannot_supply_selection_binding_approval_or_audit_fields(http, field):
    before, draft = http.state(), http.read()
    values = {'task_ids': [task['task_id'] for task in draft['preview']['tasks']], 'approved': True}
    assert_human_apply_error(http.apply(json={'generation': 2, field: values.get(field, 'human')}), 400, 'INVALID_ARGUMENT')
    unchanged(http, before, draft)


@pytest.mark.parametrize('content', ['', '{"generation":', '{"generation":2,}', 'null', '[]', '"2"', 'true'])
def test_apply_requires_a_json_object_not_coercible_content(http, content):
    before, draft = http.state(), http.read()
    assert_human_apply_error(http.apply(content=content, headers={**headers(http.project), 'Content-Type': 'application/json'}),
                 400, 'INVALID_ARGUMENT')
    unchanged(http, before, draft)


@pytest.mark.parametrize('generation', [True, False, 2.0, 2.5, 0, -1, None, '2', [], {}])
def test_generation_is_a_positive_json_integer_not_bool_float_or_string(http, generation):
    before, draft = http.state(), http.read()
    assert_human_apply_error(http.apply(json={'generation': generation}), 400, 'INVALID_ARGUMENT')
    unchanged(http, before, draft)


def test_generation_is_required_in_body(http):
    before, draft = http.state(), http.read()
    assert_human_apply_error(http.apply(json={}), 400, 'INVALID_ARGUMENT')
    unchanged(http, before, draft)


@pytest.mark.parametrize('params', [
    pytest.param({}, id='missing-actor'),
    pytest.param({'agent_node_id': ''}, id='empty-actor'),
    pytest.param({'agent_node_id': '   '}, id='blank-actor'),
    pytest.param({'agent_node_id': 'a' * 129}, id='overlong-actor'),
    pytest.param([('agent_node_id', 'actor'), ('agent_node_id', 'actor')], id='repeated-identical-actor'),
    pytest.param([('agent_node_id', 'actor'), ('agent_node_id', 'other')], id='repeated-different-actor'),
    pytest.param({'agent_node_id': 'actor', 'generation': 2}, id='generation-query-forbidden'),
    pytest.param([('agent_node_id', 'actor'), ('generation', 2), ('generation', 2)], id='repeated-generation'),
    pytest.param({'agent_node_id': 'actor', 'caller': 'external'}, id='caller-query-forbidden'),
])
def test_apply_query_has_only_one_original_actor_and_no_generation_substitute(http, params):
    before, draft = http.state(), http.read()
    assert_human_apply_error(http.apply(params=params), 400, 'INVALID_ARGUMENT')
    unchanged(http, before, draft)


@pytest.mark.parametrize('target', ['foreign-project', 'other-actor', 'missing-actor', 'unknown-plan',
                                    'old-generation', 'future-generation'])
def test_wrong_identity_or_generation_is_stale_with_exact_reimport_hint_and_no_writes(http, target):
    before, draft = http.state(), http.read()
    project = http.foreign if target == 'foreign-project' else http.project
    plan_id = '0' * 32 if target == 'unknown-plan' else draft['plan_id']
    actor = {'other-actor': 'other', 'missing-actor': 'missing'}.get(target, 'actor')
    generation = {'old-generation': 1, 'future-generation': 3}.get(target, 2)
    response = http.client.post(f'/api/projects/{project}/plan/previews/{plan_id}/apply',
        params={'agent_node_id': actor}, headers=headers(project), json={'generation': generation})
    assert_human_apply_error(response, 409, 'PLAN_STALE')
    unchanged(http, before, draft)


@pytest.mark.parametrize('loss', ['ttl', 'cache-eviction', 'deleted-actor', 'non-agent-actor'])
def test_preview_expiration_or_missing_original_agent_never_reconstructs_permission(http, monkeypatch, loss):
    if loss == 'ttl':
        preview = agent_plan._CACHE[http.store][http.draft['plan_id']]
        monkeypatch.setattr(agent_plan, 'monotonic', lambda: preview.expires + 1)
    elif loss == 'cache-eviction':
        preview = agent_plan._CACHE[http.store][http.draft['plan_id']]
        # Exercise the real bounded cache; filler previews never enter the mutation path.
        record = {'id': preview.attachment_id, 'name': preview.name}
        for index in range(64):
            scope = (*preview.scope[:2], f'cache-filler-{index}', *preview.scope[3:])
            agent_plan.remember(http.store, scope, record, preview.digest, preview.sheet, deepcopy(preview.data))
        assert http.draft['plan_id'] not in agent_plan._CACHE[http.store]
    else:
        mutations = [{'op': 'remove_node', 'node_id': 'actor'}]
        if loss == 'non-agent-actor':
            mutations.append({'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-prompt', 'text': 'human'}})
        http.store.apply_mutations(http.project, mutations, base_revision=http.store.canvas(http.project)['revision'])
        http.store.flush(http.project)
    before = http.state()
    assert_human_apply_error(http.apply(), 409, 'PLAN_STALE')
    assert http.state() == before
    assert http.events.empty() and not http.session.confirm_handlers
