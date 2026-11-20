"""Reference rules are derived from browser #395/request-builder behavior, not the resolver's own snapshots."""
from app.services.canvas_image_refs import ReferenceGraph, prompt_parts


def image(name, **fields):
    return {'name': name, 'url': f'/images/{name}.png', **fields}


def edge(source, target='target', kind='input', **fields):
    return {'from': source, 'to': target, 'kind': kind, **fields}


def test_legacy_dedup_before_block_and_outpaint_priority_at_cap():
    node = {'id': 'target', 'inputNodeIds': ['source'], 'blockedInputRefs': ['source|0'],
            'manualInputRefs': [image('one')], 'prompt': 'original'}
    graph = {'nodes': [node, {'id': 'source', 'images': [image('one'), image('two')]}]}
    assert [ref['name'] for ref in ReferenceGraph(graph, 'p').resolve('target')['refs']] == ['two']
    node.update(blockedInputRefs=[], images=[image('required')], outpaintSize={'width': 2, 'height': 1})
    graph['nodes'][1]['images'] = [image(str(i)) for i in range(21)]
    refs = ReferenceGraph(graph, 'p').resolve('target')['refs']
    assert [ref['name'] for ref in refs] == [*[str(i) for i in range(19)], 'required']
    assert refs[-1]['role'] == 'image_20'


def test_managed_exact_assets_roles_missing_source_and_legacy_aliases():
    graph = {
        'nodes': [{'id': 'target', 'promptDraftHtml': 'Use <span class="mention-image-token" '
                   'data-url="/images/alternate.png">must not leak</span>'},
                  {'id': 'source', 'images': [image('a', asset_id='a'), image('not selected', asset_id='no')]},
                  {'id': 'ordinary', 'images': [image('alternate', asset_id='a'), image('ordinary')]}],
        'connections': [edge('ordinary'), edge('missing', agentRef=True, enabled=True, order=0, asset_id='missing'),
                        edge('source', agentRef=True, enabled=False, order=1, asset_id='no'),
                        edge('source', agentRef=True, enabled=True, order=3, asset_id='b', role='detail'),
                        edge('source', agentRef=True, enabled=True, order=2, asset_id='a', role='layout')],
    }
    req = ReferenceGraph(graph, 'p', {'b': {'name': 'archived', 'kind': 'image'}}).resolve('target')
    assert [ref['name'] for ref in req['refs']] == ['a', 'archived', 'ordinary']
    assert [ref.get('agentRefRole') for ref in req['refs']] == ['layout', 'detail', None]
    assert req['prompt'].endswith('Use 图1')


def test_round_overrides_keep_round_two_image_and_managed_selection():
    nodes = [{'id': 'target', 'type': 'smart-image', 'prompt': 'draw'},
             {'id': 'loop', 'type': 'smart-loop', 'imageInput': True},
             {'id': 'images', 'images': [image('image-one'), image('image-two')]},
             {'id': 'ordinary', 'images': [image('default')]},
             {'id': 'managed', 'images': [image('pinned', asset_id='pinned')]}]
    edges = [edge('images', 'loop'), edge('ordinary'), edge('loop', kind='flow'),
             edge('managed', agentRef=True, enabled=True, asset_id='pinned', order=1, role='detail')]
    graph = ReferenceGraph({'nodes': nodes, 'connections': edges}, 'p')
    refs = lambda req: [ref['name'] for ref in req['refs']]
    assert refs(graph.resolve('target')) == ['pinned', 'default']
    assert refs(graph.resolve('target', ctx={'index': 1}, override_source='loop')) == ['pinned', 'image-one']
    assert refs(graph.resolve('target', ctx={'index': 2}, override_source='loop')) == ['pinned', 'image-two']
    assert refs(graph.resolve('target', ctx={'roundOutputs': {'ordinary': [image('fresh')]}},
                              override_source='ordinary')) == ['pinned', 'fresh']


def test_local_ownership_is_not_inferred_from_external_routes_or_bare_paths():
    prefix = '/api/project-library/p/media/'
    urls = [prefix + 'owned/preview?w=32', f'https://example.invalid{prefix}remote',
            'D:/arbitrary.png', '/api/project-library/other/media/cross-project', '/assets/local.png']
    graph = ReferenceGraph({'nodes': [{'id': 'target', 'manualInputRefs': [{'url': url} for url in urls]}]}, 'p')
    assert list(graph.owned_image_ids(graph.resolve('target'))) == [('owned', None)] + [('', None)] * 4


def test_editor_html_token_contents_are_opaque_and_literal_at_text_stays_literal():
    html = ('Use &lt;literal&gt;<div>@not-a-token <span class="mention-image-token" '
            'data-url="/assets/legacy.png?a=1&amp;b=2" data-name="A &amp; B">'
            '<script>opaque()</script><strong><img src="https://example.invalid"></strong></span></div><br>End')
    parts = prompt_parts(html)
    mention, = [part for part in parts if 'url' in part]
    assert mention['name'] == 'A & B' and mention['url'] == '/assets/legacy.png?a=1&b=2'
    request = ReferenceGraph({'nodes': [{'id': 'target', 'promptDraftHtml': html}]}, 'p').resolve('target')
    assert request['displayPrompt'] == 'Use <literal>\n@not-a-token @A & B\n\nEnd'
    assert request['prompt'].endswith('Use <literal>\n@not-a-token 图1\n\nEnd')
