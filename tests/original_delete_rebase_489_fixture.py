"""#489 private stdio fixture: actual Store CAS, generation, files and deletion transactions.

This is not HTTP/browser evidence. No providers, service or fabricated conflicts.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from firefly_projects import ProjectStore, ProjectStoreError, RevisionConflict


def build_original_delete_fixture_png(index):
    data = io.BytesIO()
    Image.new('RGB', (8, 8), (index * 31 % 255, 70, 90)).save(data, format='PNG')
    return data.getvalue()


def run_original_delete_fixture(root, action, request):
    store = ProjectStore(root / 'projects', snapshot_delay=0)
    state_path = root / 'fixture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not state:
        state['pid'] = store.create_project('e2e-tabbit-private-489')['id']
    pid = state['pid']

    def canvas():
        return store.open_project(pid)['canvas']

    def generate(node_id, index):
        task = f'task-{index}'
        store.begin_image_generation(pid, node_id, {'taskId': task, 'kind': 'image', 'count': 1},
                                     base_revision=canvas()['revision'])
        media = store.import_media(pid, build_original_delete_fixture_png(index), f'generated-{index}.png', 'image/png',
                                   {'type': 'online_generation', 'task_id': task})
        result = store.complete_image_generation(pid, node_id, [{'asset_id': media['asset_id'], 'kind': 'image'}],
            {'id': f'history-{index}', 'status': 'succeeded', 'result_node_id': node_id,
             'task_id': task, 'prompt': f'private generation {index}',
             'outputs': [{'asset_id': media['asset_id'], 'kind': 'image'}]}, task_id=task, require_pending=True)
        return next(record for record in result['history'] if record['id'] == f'history-{index}')['outputs'][0]

    if action == 'seed':
        store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {'id': name, 'type': 'smart-image', 'images': [], 'userTouchedAt': 1234}}
                                   for name in ('target', 'copy', 'background')], base_revision=0)
        output = generate('target', 1)
        copied = next(node for node in canvas()['nodes'] if node['id'] == 'copy')
        copied['images'] = [copy.deepcopy(output)]
        store.apply_mutations(pid, [{'op': 'upsert_node', 'node': copied}], base_revision=canvas()['revision'])
        state.update(asset_id=output['asset_id'], output_id=output['output_id'], injections=0)
        result = {'canvas': canvas(), **state}
    elif action == 'delete':
        injection = request.get('injection')
        if injection:
            state['injections'] += 1
            current = canvas()
            if injection == 'generation':
                generate('background', state['injections'] + 1)
            elif injection == 'target-history':
                target = next(node for node in current['nodes'] if node['id'] == 'target')
                history = next(record for record in current['history'] if record['id'] == 'history-1')
                history['prompt'] = 'new history data'
                store.complete_image_generation(pid, 'target', target['images'], history, task_id='task-1')
                assert next(record for record in canvas()['history'] if record['id'] == 'history-1')['prompt'] == 'new history data'
            elif injection == 'already-deleted':
                store.delete_generated_original(pid, state['asset_id'], base_revision=current['revision'],
                    history_id='history-1', output_id=state['output_id'], output_index=0)
            else:
                target = next(node for node in current['nodes'] if node['id'] == 'target')
                if injection == 'target-node':
                    target['runPrompt'] = 'new human data'
                    mutations = [{'op': 'upsert_node', 'node': target}]
                elif injection == 'new-node-reference':
                    mutations = [{'op': 'upsert_node', 'node': {'id': 'new-reference', 'type': 'smart-image',
                        'images': [{'asset_id': state['asset_id'], 'kind': 'image'}]}}]
                elif injection == 'new-history-reference':
                    current['history'].append({'id': 'new-history', 'status': 'succeeded',
                        'inputs': [{'asset_id': state['asset_id']}], 'outputs': []})
                    mutations = [{'op': 'set_canvas_meta', 'history': current['history']}]
                elif injection == 'new-mask-reference':
                    background = next(node for node in current['nodes'] if node['id'] == 'background')
                    background['nested'] = {'maskAssetId': state['asset_id']}
                    mutations = [{'op': 'upsert_node', 'node': background}]
                else:
                    raise AssertionError(injection)
                store.apply_mutations(pid, mutations, base_revision=current['revision'])
        before_delete = canvas()
        payload = request['payload']
        try:
            if request['mode'] == 'wide':
                deleted = store.delete_generated_original_project_wide(pid, payload['asset_id'],
                    base_revision=payload['base_revision'])
            else:
                deleted = store.delete_generated_original(pid, payload['asset_id'], base_revision=payload['base_revision'],
                    history_id=payload['history_id'], output_id=payload['output_id'], output_index=payload['output_index'])
            result = {'status': 200, 'data': {'ok': True, **deleted}}
        except RevisionConflict as error:
            result = {'status': 409, 'data': {'detail': {'message': str(error), 'canvas': error.canvas}}}
        except ProjectStoreError as error:
            result = {'status': 409, 'data': {'detail': str(error)}}
        result['before_delete'] = before_delete
        result['after_delete'] = canvas()
    elif action == 'edit-target':
        current = canvas()
        target = next(node for node in current['nodes'] if node['id'] == 'target')
        target['runPrompt'] = 'newer target edit'
        store.apply_mutations(pid, [{'op': 'upsert_node', 'node': target}], base_revision=current['revision'])
        result = {'canvas': canvas()}
    elif action == 'inspect':
        project_dir = root / 'projects' / pid
        result = {'canvas': canvas(), 'media': store.list_media(pid), 'trash': store.list_deleted_media(pid),
                  'files': {path.relative_to(project_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                            for directory in ('media', 'trash/media') for path in (project_dir / directory).glob('*') if path.is_file()}}
    else:
        raise AssertionError(action)
    store.flush(pid)
    state_path.write_text(json.dumps(state), encoding='utf-8')
    store.close()
    return result


if __name__ == '__main__':
    print(json.dumps(run_original_delete_fixture(Path(sys.argv[1]), sys.argv[2], json.loads(sys.stdin.read() or '{}'))))
