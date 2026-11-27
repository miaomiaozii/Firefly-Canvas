import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from firefly_projects import ProjectStore
from app.services.generation import archive_project_generated_image


LIBRARY = '/api/project-library'


def cover_fixture_png(color):
    buffer = BytesIO()
    Image.new('RGB', (50, 30), color).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def cover_library(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    with TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client:
        yield store, client
    store.close()


def seed_cover_outputs(store, project_id):
    # Finish in reverse arrival order: history's generation timestamps should win.
    assets = {}
    for index in (2, 1):
        node = f'node-{index}'
        source = base64.b64encode(cover_fixture_png((50 * index, 30, 90))).decode('ascii')
        asset = asyncio.run(archive_project_generated_image(project_id, {'type': 'b64', 'value': source}, index))['asset_id']
        output = {'asset_id': asset, 'kind': 'image'}
        store.complete_image_generation(project_id, node, [output], {
            'id': f'history-{index}', 'result_node_id': node, 'created_at': index,
            'type': 'online-image', 'status': 'succeeded', 'outputs': [output],
        })
        assets[index] = asset
    store.flush(project_id)
    return assets[1], assets[2]


def read_cover(client, project_id, *, trash=False):
    response = client.get(LIBRARY + ('/trash' if trash else ''))
    assert response.status_code == 200, response.text
    record = next(record for record in response.json()['projects'] if record['id'] == project_id)
    return record['cover_thumbnail_url']


@pytest.mark.parametrize('removed', [0, 1, 2], ids=['first-present', 'second-survives', 'all-deleted'])
def test_cover_follows_generation_order_not_current_node_order(cover_library, removed):
    store, client = cover_library
    project_id = store.create_project('cover order')['id']
    first, second = seed_cover_outputs(store, project_id)
    store.apply_mutations(project_id, [{'op': 'upsert_node', 'node': {'id': 'keep', 'type': 'smart-image'}}],
                          base_revision=store.canvas(project_id)['revision'])
    for index in range(1, removed + 1):
        revision = store.canvas(project_id)['revision']
        store.apply_mutations(project_id, [{'op': 'remove_node', 'node_id': f'node-{index}'}], base_revision=revision)
    with patch.object(Image, 'open', side_effect=AssertionError('list decoded an image')):
        cover = read_cover(client, project_id)
    expected = (first, second, None)[removed]
    assert cover == (f'{LIBRARY}/{project_id}/cover/{expected}' if expected else '')


def test_no_generated_image_does_not_use_an_import(cover_library):
    store, client = cover_library
    project_id = store.create_project('cover none')['id']
    asset = store.import_media(project_id, cover_fixture_png('red'), 'reference.png', 'image/png')['asset_id']
    store.apply_mutations(project_id, [{'op': 'upsert_node', 'node': {'id': 'import', 'type': 'image',
                          'images': [{'asset_id': asset, 'kind': 'image'}]}}], base_revision=0)
    assert read_cover(client, project_id) == ''


def test_cold_cover_is_generated_once_and_trash_serves_existing_webp(cover_library):
    store, client = cover_library
    project_id = store.create_project('cover cache')['id']
    first, _ = seed_cover_outputs(store, project_id)
    preview = store.root / project_id / 'previews' / f'{first}-512.webp'
    assert preview.is_file(), 'new generation did not prewarm'
    preview.unlink()
    cover = read_cover(client, project_id)
    assert not preview.exists(), 'listing warmed a cold cover'
    with patch.object(Image, 'open', wraps=Image.open) as opened:
        with ThreadPoolExecutor(max_workers=4) as workers:
            images = list(workers.map(client.get, [cover] * 4))
        assert opened.call_count == 1
    assert all(image.status_code == 200 and image.headers['content-type'] == 'image/webp' for image in images)
    assert preview.read_bytes() == images[0].content
    assert all(image.content == images[0].content for image in images)
    assert client.get(cover, headers={'Origin': 'https://unrelated.invalid'}).status_code == 403
    with patch.object(Image, 'open', side_effect=AssertionError('cache hit decoded the original')):
        assert client.get(cover).content == images[0].content
        alternate = preview.with_name(f'{first}-384.webp')
        preview.rename(alternate)
        assert client.get(cover).content == images[0].content
        assert not preview.exists()
        store.trash_project(project_id)
        assert client.get(cover).status_code == 404
        trashed = read_cover(client, project_id, trash=True)
        assert trashed == f'{LIBRARY}/trash/{project_id}/cover/{first}'
        assert client.get(trashed).content == images[0].content


def test_missing_media_and_deleted_output_are_not_served_as_stale_covers(cover_library):
    store, client = cover_library
    project_id = store.create_project('cover missing')['id']
    first, second = seed_cover_outputs(store, project_id)
    stale = read_cover(client, project_id)
    Path(store.media_record(project_id, first)['path']).unlink()
    assert read_cover(client, project_id).endswith(f'/{second}')
    assert client.get(stale).status_code == 404
    canvas = store.canvas(project_id)
    output = next(row['outputs'][0] for row in canvas['history'] if row['id'] == 'history-2')
    store.delete_generated_original(project_id, second, base_revision=canvas['revision'],
                                    history_id='history-2', output_id=output['output_id'], output_index=0)
    assert read_cover(client, project_id) == ''
    assert client.get(f'{LIBRARY}/{project_id}/cover/{second}').status_code == 404
