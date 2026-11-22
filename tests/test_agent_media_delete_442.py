"""Agent recycle identities are recoverable, not authority to overwrite resources."""
import asyncio
from copy import deepcopy
import json
import os
import threading

import pytest
from fastapi import HTTPException

import main
import firefly_projects as projects
from app.constants import NODE_TYPE_IMAGE
from app.routers import api_agent_tools as tools, api_mcp
from app.services import agent_ops_store
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_undo import undo_ops
from firefly_projects import ProjectStore
from firefly_opsdb import OpsDb
from tests.test_agent_ops_media_e2e_393 import env, png
from tests.test_agent_batch_400 import fx, confirm
from tests.test_agent_generation_failures_401 import operation_results


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def scope(project, tool='media.delete', args=None):
    return agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn-442', tool=tool, args=args or {})


def delete(store, project, asset):
    async def run():
        with scope(project, args={'asset_id': asset}):
            return await tools.delete_media_core(project, asset)
    return asyncio.run(run())


def setup(store, project, generated=False, color='red'):
    record = store.import_media(project, png(color), 'original.png', 'image/png',
                                {'type': 'online_generation' if generated else 'local_upload', 'name': 'original'})
    asset = record['asset_id']
    canvas = store.canvas(project)
    store.apply_mutations(project, [
        {'op': 'upsert_node', 'node': {'id': color, 'type': NODE_TYPE_IMAGE, 'prompt': 'keep me',
            'images': [{'asset_id': asset, 'kind': 'image', 'name': 'original.png'}]}},
        {'op': 'set_canvas_meta', 'history': [{'id': color, 'result_node_id': color, 'status': 'succeeded',
            'outputs': [{'asset_id': asset, 'kind': 'image', 'name': 'original.png'}]}]},
    ], base_revision=canvas['revision'])
    store.flush(project)
    directory = store.root / project
    preview = directory / 'previews' / f'{asset}-test.webp'
    preview.parent.mkdir(exist_ok=True)
    preview.write_bytes(b'synthetic preview')
    return asset, deepcopy(store._load_media_index(project)['items'][asset]), preview


@pytest.mark.parametrize('generated', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_exact_bytes_metadata_semantics_and_idempotent_undo(env, monkeypatch, generated, restart):
    store, project, db = env
    asset, original, preview = setup(store, project, generated)
    directory = store.root / project
    before = store.canvas(project)
    result = delete(store, project, asset)
    record = agent_ops_store.get_operation(db, project, result['op_id'])
    assert record['irreversible_effects'] == [] and record['touched_node_ids'] == []
    assert record['before'][0]['entity'] == 'media'
    assert 'filename' not in json.dumps(record['before'])
    assert asset not in read_json(directory / 'media/index.json')['items']
    item = store.list_deleted_media(project)[0]
    assert item['agent_deletion_id'] == result['op_id'] and item['reason'] == 'agent-media-delete'
    assert (directory / 'trash/media' / original['filename']).read_bytes() == png('red')
    assert (directory / 'trash/previews' / preview.name).read_bytes() == b'synthetic preview'
    assert not preview.exists()
    assert store.canvas(project)['nodes'] == before['nodes']
    assert store.canvas(project)['history'] == before['history']
    if restart:
        store.close()
        store = ProjectStore(store.root, snapshot_delay=0)
        monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    try:
        current = store.canvas(project)
        node = deepcopy(current['nodes'][0])
        node.update(prompt='human edit', x=442, userTouchedAt=123, images=[])
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=current['revision'])
        store.flush(project)
        undo = undo_ops(store, project, op_id=result['op_id'])
        assert undo['status'] == 'reverted' and undo['reverted'] == [result['op_id']]
        assert undo['irreversible_effects'] == [] and undo['broadcast_op_ids'] == [result['op_id']]
        assert store._load_media_index(project)['items'][asset] == original
        assert (directory / 'media' / original['filename']).read_bytes() == png('red')
        assert preview.read_bytes() == b'synthetic preview'
        assert store.canvas(project)['nodes'][0] == node
        assert store.canvas(project)['history'] == before['history']
        assert store.list_deleted_media(project) == []
        assert not (directory / 'trash/undo' / item['undo']['snapshot_filename']).exists()
        repeated = undo_ops(store, project, op_id=result['op_id'])
        assert repeated['status'] == 'already_reverted' and repeated['already_reverted'] == [result['op_id']]
    finally:
        if restart:
            store.close()


@pytest.mark.parametrize('conflict', ['clear', 'expired', 'missing-bytes', 'changed-bytes', 'missing-snapshot',
                                      'live-file', 'live-preview', 'live-index', 'alias-index'])
def test_missing_or_occupied_recovery_never_overwrites_or_marks_success(env, conflict):
    store, project, db = env
    asset, original, preview = setup(store, project)
    result = delete(store, project, asset)
    directory = store.root / project
    if conflict == 'clear':
        store.purge_deleted_media(project)
    elif conflict == 'expired':
        index = store._load_media_trash_index(project)
        index['items'][0]['expires_at'] = 1
        projects._write_json_atomic(directory / 'trash/index.json', index, keep_backup=False)
    elif conflict in ('missing-bytes', 'changed-bytes'):
        path = directory / 'trash/media' / original['filename']
        if conflict == 'missing-bytes':
            path.unlink()
        else:
            path.write_bytes(b'changed trash bytes')
    elif conflict == 'missing-snapshot':
        item = store.list_deleted_media(project)[0]
        (directory / 'trash/undo' / item['undo']['snapshot_filename']).unlink()
    elif conflict == 'live-file':
        (directory / 'media' / original['filename']).write_bytes(b'new user bytes')
    elif conflict == 'live-preview':
        preview.write_bytes(b'new user preview')
    else:
        index = store._load_media_index(project)
        index['items'][asset if conflict == 'live-index' else 'another-asset'] = deepcopy(original)
        projects._write_json_atomic(directory / 'media/index.json', index, keep_backup=False)
    before = {path.relative_to(directory).as_posix(): path.read_bytes()
              for sub in ('media', 'previews', 'trash') for path in (directory / sub).rglob('*') if path.is_file()}
    outcome = undo_ops(store, project, op_id=result['op_id'])
    assert outcome['status'] == 'conflict' and outcome['reverted'] == []
    assert not agent_ops_store.get_operation(db, project, result['op_id'])['undone']
    after = {path.relative_to(directory).as_posix(): path.read_bytes()
             for sub in ('media', 'previews', 'trash') for path in (directory / sub).rglob('*') if path.is_file()}
    assert after == before


def test_turn_group_preflights_every_media_and_canvas_inverse(env):
    store, project, db = env
    first, original, _ = setup(store, project)
    second, second_record, _ = setup(store, project, color='blue')
    # These undo positives need a real actor and an actor-owned sibling update (#486).
    canvas = store.canvas(project)
    target = store.find_canvas_node(project, 'red')
    store.apply_mutations(project, [
        {'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-agent'}},
        {'op': 'upsert_node', 'node': {**target, 'createdBy': 'agent', 'agentNodeId': 'actor'}},
    ], base_revision=canvas['revision'])
    async def run():
        with scope(project, 'canvas.update_node'):
            updated = await tools.update_node_core(project, 'red', {'prompt': 'agent change'})
        with scope(project):
            deleted = await tools.delete_media_core(project, first)
        with scope(project):
            other = await tools.delete_media_core(project, second)
        return updated, deleted, other
    updated, deleted, other = asyncio.run(run())
    directory = store.root / project
    collision = directory / 'media' / second_record['filename']
    collision.write_bytes(b'human resource')
    outcome = undo_ops(store, project, turn_id='turn-442')
    assert outcome['status'] == 'conflict' and outcome['reverted'] == []
    assert not (directory / 'media' / original['filename']).exists()
    assert store.find_canvas_node(project, 'red')['prompt'] == 'agent change'
    assert collision.read_bytes() == b'human resource'
    collision.unlink()
    outcome = undo_ops(store, project, turn_id='turn-442')
    assert outcome['status'] == 'reverted', outcome
    assert set(outcome['reverted']) == {updated['op_id'], deleted['op_id'], other['op_id']}
    assert store.find_canvas_node(project, 'red')['prompt'] == 'keep me'
    assert store._load_media_index(project)['items'][first] == original
    assert store._load_media_index(project)['items'][second] == second_record
    assert store.list_deleted_media(project) == []


@pytest.mark.parametrize('stage', ['after_journal', 'after_media_move', 'after_media_index', 'after_trash_index',
                                    'after_canvas', 'after_history', 'after_undo_snapshot', 'after_commit'])
def test_delete_transaction_error_rolls_back_without_success_log(env, monkeypatch, stage):
    store, project, db = env
    asset, original, preview = setup(store, project)
    def fail(current):
        if current == stage:
            raise OSError('synthetic transaction failure')
    monkeypatch.setattr(store, '_original_transaction_checkpoint', fail)
    with pytest.raises(HTTPException):
        delete(store, project, asset)
    assert store._load_media_index(project)['items'][asset] == original
    assert (store.root / project / 'media' / original['filename']).read_bytes() == png('red')
    assert preview.read_bytes() == b'synthetic preview'
    assert store.list_deleted_media(project) == []
    assert agent_ops_store.list_operations(db, project) == []
    assert not store._original_transaction_path(project).exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
@pytest.mark.parametrize('writer', ['canvas', 'flush', 'import'])
def test_blocked_native_rollback_cannot_erase_later_accepted_writes(env, writer):
    store, project, _ = env
    asset, _, _ = setup(store, project)
    directory = store.root / project

    def write():
        if writer == 'import':
            return store.import_media(project, png('blue'), 'later.png', 'image/png')['asset_id']
        if writer == 'flush':
            return store.flush(project)
        canvas = store.canvas(project)
        node = deepcopy(canvas['nodes'][0])
        node['prompt'] = 'human saved after the failed deletion'
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
        store.flush(project)

    with (directory / 'trash/index.json').open('rb'):
        with pytest.raises(HTTPException):
            delete(store, project, asset)
        assert store._original_transaction_path(project).exists()
        with pytest.raises(projects.ProjectStoreError):
            write()
    accepted = False
    imported = None
    try:
        imported = write()
        accepted = True
    except projects.ProjectStoreError:
        pass
    if accepted:
        assert not store._original_transaction_path(project).exists()
    reopened = ProjectStore(store.root, snapshot_delay=0)
    try:
        expected = 'human saved after the failed deletion' if accepted and writer == 'canvas' else 'keep me'
        assert reopened.find_canvas_node(project, 'red')['prompt'] == expected
        if accepted and writer == 'import':
            assert reopened.media_record(project, imported)
    finally:
        reopened.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
@pytest.mark.parametrize('entry', ['store', 'route'])
@pytest.mark.parametrize('document', ['canvas.json', 'project.json'])
def test_pending_media_recovery_fences_backup_restore(env, entry, document):
    from app.routers import api_canvases

    store, project, _ = env
    asset, _, _ = setup(store, project)
    directory = store.root / project
    backup = document + '.bak'
    assert (directory / backup).is_file()

    def restore():
        if entry == 'store':
            return store.restore_from_backup(project, document, backup)
        payload = api_canvases.CanvasRecoveryRestoreRequest(file=document, backup=backup)
        return asyncio.run(api_canvases.restore_canvas_from_backup(project, payload))['restored']

    with (directory / 'trash/index.json').open('rb'):
        with pytest.raises(HTTPException):
            delete(store, project, asset)
        assert store._original_transaction_path(project).exists()
        before_primary = (directory / document).read_bytes()
        before_backup = (directory / backup).read_bytes()
        with pytest.raises(projects.ProjectStoreError if entry == 'store' else HTTPException):
            restore()
        assert (directory / document).read_bytes() == before_primary
        assert (directory / backup).read_bytes() == before_backup
    result = restore()
    assert result['restored_from'] == backup
    assert not store._original_transaction_path(project).exists()
    accepted = read_json(directory / document)
    store.canvas(project)
    assert read_json(directory / document) == accepted


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize('stage, deleted', [('after_canvas', False), ('after_commit', True)])
def test_live_store_recovers_journal_before_accepting_new_edits(env, monkeypatch, stage, deleted):
    store, project, _ = env
    asset, _, _ = setup(store, project)

    def crash(checkpoint):
        if checkpoint == stage:
            raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(store, '_original_transaction_checkpoint', crash)
        with pytest.raises(Interrupted):
            store.recycle_agent_media(project, asset, deletion_id='interrupted-live')
    assert store._original_transaction_path(project).exists()
    canvas = store.canvas(project)
    assert not store._original_transaction_path(project).exists()
    assert (asset not in store._load_media_index(project)['items']) is deleted
    node = deepcopy(canvas['nodes'][0])
    node['prompt'] = 'human edit after native recovery'
    if deleted:
        node['images'] = []
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
    store.flush(project)
    reopened = ProjectStore(store.root, snapshot_delay=0)
    try:
        assert reopened.find_canvas_node(project, 'red')['prompt'] == node['prompt']
        assert (asset not in reopened._load_media_index(project)['items']) is deleted
    finally:
        reopened.close()


@pytest.mark.parametrize('restoring', [False, True])
def test_interrupted_native_transaction_recovers_without_sidecar_authority(env, monkeypatch, restoring):
    store, project, db = env
    asset, original, _ = setup(store, project)
    if restoring:
        result = delete(store, project, asset)
    def crash(stage):
        if stage == 'after_canvas':
            raise Interrupted()
    monkeypatch.setattr(store, '_original_transaction_checkpoint', crash)
    with pytest.raises(Interrupted):
        if restoring:
            undo_ops(store, project, op_id=result['op_id'])
        else:
            store.recycle_agent_media(project, asset, deletion_id='interrupted')
    recovered = ProjectStore(store.root, snapshot_delay=0)
    try:
        directory = recovered.root / project
        if restoring:
            assert asset not in recovered._load_media_index(project)['items']
            assert (directory / 'trash/media' / original['filename']).read_bytes() == png('red')
            assert not agent_ops_store.get_operation(db, project, result['op_id'])['undone']
            assert undo_ops(recovered, project, op_id=result['op_id'])['status'] == 'reverted'
        else:
            assert recovered._load_media_index(project)['items'][asset] == original
            assert (directory / 'media' / original['filename']).read_bytes() == png('red')
            assert recovered.list_deleted_media(project) == []
            assert agent_ops_store.list_operations(db, project) == []
    finally:
        recovered.close()


def test_sidecar_append_failure_blocks_entire_turn_but_preserves_native_recovery(env, monkeypatch):
    store, project, db = env
    asset, _, _ = setup(store, project)
    # These undo positives need a real actor and an actor-owned sibling update (#486).
    canvas = store.canvas(project)
    target = store.find_canvas_node(project, 'red')
    store.apply_mutations(project, [
        {'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-agent'}},
        {'op': 'upsert_node', 'node': {**target, 'createdBy': 'agent', 'agentNodeId': 'actor'}},
    ], base_revision=canvas['revision'])
    async def update():
        with scope(project, 'canvas.update_node'):
            return await tools.update_node_core(project, 'red', {'prompt': 'agent change'})
    sibling = asyncio.run(update())
    with monkeypatch.context() as patch:
        patch.setattr(agent_ops_store, 'append_operation', lambda *a, **kw: (_ for _ in ()).throw(OSError('sidecar failure')))
        result = delete(store, project, asset)
    assert agent_ops_store.get_operation(db, project, result['op_id']) is None
    assert undo_ops(store, project, turn_id='turn-442')['status'] == 'conflict'
    assert undo_ops(store, project, op_id=sibling['op_id'])['status'] == 'conflict'
    assert store.list_deleted_media(project)[0]['asset_id'] == asset
    store.restore_trash_media(project, asset, base_revision=store.canvas(project)['revision'])
    assert store.media_record(project, asset)


def test_registration_failure_remains_guarded_during_recovery(env, monkeypatch):
    from app.services.agent_ops_journal import PendingOperation
    store, project, db = env
    asset, _, _ = setup(store, project)
    def fail(*args, **kwargs):
        raise OSError('registration failure')
    monkeypatch.setattr(PendingOperation, 'register', fail)
    async def run():
        with scope(project) as context:
            with pytest.raises(HTTPException) as error:
                await tools.delete_media_core(project, asset)
            assert error.value.detail['details']['status'] == 'partial-unknown'
            assert undo_ops(store, project, op_id=context.op_id)['status'] in ('conflict', 'not_found')
            assert undo_ops(store, project, turn_id='turn-442')['status'] == 'conflict'
            for _ in range(100):
                if agent_ops_store.get_operation(db, project, context.op_id) is not None:
                    break
                await asyncio.sleep(.02)
            assert agent_ops_store.get_operation(db, project, context.op_id) is not None
            assert undo_ops(store, project, op_id=context.op_id)['status'] == 'conflict'
            assert store.list_deleted_media(project)[0]['asset_id'] == asset
    asyncio.run(run())


def test_pending_flush_blocks_undo_until_observed_then_restores(env, monkeypatch):
    store, project, db = env
    asset, _, _ = setup(store, project)
    original_flush = store.flush
    async def run():
        monkeypatch.setattr(store, 'flush', lambda *a: (_ for _ in ()).throw(OSError('before replacement')))
        with scope(project) as context:
            with pytest.raises(HTTPException) as error:
                await tools.delete_media_core(project, asset)
            assert error.value.detail['details']['status'] == 'partial-unknown'
            assert undo_ops(store, project, op_id=context.op_id)['status'] == 'pending'
            monkeypatch.setattr(store, 'flush', original_flush)
            store.flush(project)
            for _ in range(100):
                if not tools.agent_generation_is_running(store, project, context.op_id):
                    break
                await asyncio.sleep(.02)
            assert undo_ops(store, project, op_id=context.op_id)['status'] == 'reverted'
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['restore-write', 'mark-undone'])
def test_undo_error_does_not_falsely_mark_reverted(env, monkeypatch, failure):
    store, project, db = env
    asset, original, _ = setup(store, project)
    result = delete(store, project, asset)
    with monkeypatch.context() as patch:
        if failure == 'restore-write':
            def fail(stage):
                if stage == 'after_media_index':
                    raise OSError('restore failed')
            patch.setattr(store, '_original_transaction_checkpoint', fail)
        else:
            patch.setattr(agent_ops_store, 'mark_undone', lambda *a: (_ for _ in ()).throw(OSError('mark failed')))
        outcome = undo_ops(store, project, op_id=result['op_id'])
    assert outcome['status'] == 'error' and outcome['reverted'] == []
    assert not agent_ops_store.get_operation(db, project, result['op_id'])['undone']
    if failure == 'restore-write':
        assert store.list_deleted_media(project)[0]['asset_id'] == asset
        assert undo_ops(store, project, op_id=result['op_id'])['status'] == 'reverted'
    else:
        assert store._load_media_index(project)['items'][asset] == original
        assert undo_ops(store, project, op_id=result['op_id'])['status'] == 'conflict'


def test_cancel_during_native_write_waits_for_recoverable_settlement(env, monkeypatch):
    store, project, db = env
    asset, _, _ = setup(store, project)
    entered, release = threading.Event(), threading.Event()
    original = store.recycle_agent_media
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'recycle_agent_media', blocked)
    async def run():
        with scope(project) as context:
            task = asyncio.create_task(tools.delete_media_core(project, asset))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done()
            release.set()
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.detail['details']['op_id'] == context.op_id
            assert agent_ops_store.get_operation(db, project, context.op_id)
            assert undo_ops(store, project, op_id=context.op_id)['status'] == 'reverted'
    asyncio.run(run())


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('approved', [False, True])
def test_transport_safety_floor_late_decision_and_actual_recycle(fx, monkeypatch, operation_results, transport, autonomy, approved):
    fx.session.autonomy = autonomy
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    monkeypatch.setattr(api_mcp, 'CONFIRM_TIMEOUT_SECONDS', .01)
    asset = fx.ids[0]
    directory = fx.store.root / fx.pid
    original = deepcopy(fx.store._load_media_index(fx.pid)['items'][asset])
    if transport == 'rest':
        response = fx.client.delete(f'/api/projects/{fx.pid}/media/{asset}?agent_node_id=actor',
                                    headers={'X-Agent-Project': fx.pid})
        receipt = response.json()
    else:
        response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor&turn_id=external-442',
            headers={'Mcp-Session-Id': 'delete-442'}, json={'jsonrpc': '2.0', 'id': 442, 'method': 'tools/call',
                'params': {'name': 'media.delete', 'arguments': {'asset_id': asset}}})
        payload = response.json()['result']
        assert not payload['isError'], payload
        receipt = json.loads(payload['content'][0]['text'])
    assert response.status_code == 200 and receipt['code'] == 'CONFIRM_PENDING', receipt
    event = fx.events.get(timeout=3)
    assert event['confirm_id'] == receipt['confirm_id']
    assert (directory / 'media' / original['filename']).is_file()
    assert not fx.store.list_deleted_media(fx.pid)
    assert agent_ops_store.list_operations(OpsDb(fx.store.root), fx.pid) == []
    confirm(fx, event, approved)
    outcome = operation_results.get(timeout=5)
    if not approved:
        assert 'error' in outcome, outcome
        assert outcome['error']['code'] == 'CONFIRM_REJECTED' or '拒绝' in str(outcome), outcome
        assert (directory / 'media' / original['filename']).is_file()
        assert not fx.store.list_deleted_media(fx.pid)
        assert agent_ops_store.list_operations(OpsDb(fx.store.root), fx.pid) == []
        return
    assert outcome.get('success'), outcome
    op_id = outcome['op_id']
    assert not (directory / 'media' / original['filename']).exists()
    assert (directory / 'trash/media' / original['filename']).is_file()
    record = agent_ops_store.get_operation(OpsDb(fx.store.root), fx.pid, op_id)
    assert record['turn_id'] == ('initiating-turn' if transport == 'rest' else 'external-442')
    confirm(fx, event)
    assert len(agent_ops_store.list_operations(OpsDb(fx.store.root), fx.pid)) == 1
    if transport == 'rest':
        response = fx.client.post(f'/api/projects/{fx.pid}/ops/undo?agent_node_id=actor',
                                  headers={'X-Agent-Project': fx.pid}, json={'op_id': op_id})
    else:
        response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor', headers={'Mcp-Session-Id': 'delete-442'},
            json={'jsonrpc': '2.0', 'id': 443, 'method': 'tools/call',
                  'params': {'name': 'ops.undo', 'arguments': {'op_id': op_id}}})
    assert response.is_success, response.text
    result = response.json()
    if transport == 'mcp':
        assert not result['result']['isError'], result
        result = json.loads(result['result']['content'][0]['text'])
    assert result['status'] == 'reverted', result
    assert fx.store._load_media_index(fx.pid)['items'][asset] == original
    assert (directory / 'media' / original['filename']).is_file()


@pytest.mark.parametrize('occupied', ['trash-file', 'trash-entry', 'index-path'])
def test_recycle_does_not_overwrite_existing_resources(env, occupied):
    store, project, db = env
    asset, original, _ = setup(store, project)
    directory = store.root / project
    if occupied == 'trash-file':
        target = directory / 'trash/media' / original['filename']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'previous recycle bytes')
    elif occupied == 'trash-entry':
        projects._write_json_atomic(directory / 'trash/index.json',
            {'items': [{'asset_id': asset, 'expires_at': 4102444800000, 'media_retained': True}]}, keep_backup=False)
    else:
        index = store._load_media_index(project)
        index['items'][asset]['filename'] = '../canvas.json'
        projects._write_json_atomic(directory / 'media/index.json', index, keep_backup=False)
    canvas_bytes = (directory / 'canvas.json').read_bytes()
    with pytest.raises(HTTPException):
        delete(store, project, asset)
    assert (directory / 'media' / original['filename']).read_bytes() == png('red')
    assert (directory / 'canvas.json').read_bytes() == canvas_bytes
    assert agent_ops_store.list_operations(db, project) == []
    if occupied == 'trash-file':
        assert target.read_bytes() == b'previous recycle bytes'


def test_native_recycle_panel_restore_and_non_agent_permanent_delete(env):
    store, project, db = env
    asset, original, _ = setup(store, project)
    result = delete(store, project, asset)
    restored = store.restore_trash_media(project, asset, base_revision=store.canvas(project)['revision'])
    assert restored['restored'] == asset and store._load_media_index(project)['items'][asset] == original
    assert undo_ops(store, project, op_id=result['op_id'])['status'] == 'conflict'
    store.delete_project_media_asset(project, asset, base_revision=store.canvas(project)['revision'])
    assert not store.list_deleted_media(project)
    assert not (store.root / project / 'media' / original['filename']).exists()
    another = store.import_media(project, png('blue'), 'other.png', 'image/png')
    asyncio.run(tools.delete_media_core(project, another['asset_id']))
    assert not store.list_deleted_media(project)
    assert not (store.root / project / 'media' / another['filename']).exists()
