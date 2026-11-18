"""Issue #437: retained shared originals remain readable from the recycle bin."""
import copy
import json
import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import main
from firefly_projects import ProjectStore, ProjectStoreError


class RetainedTrashTests(unittest.TestCase):
    def setUp(self):
        root = os.environ.get('FIREFLY_PROJECTS_ROOT')
        if root:
            Path(root).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='issue-437-', dir=root)
        self.store = ProjectStore(Path(self.temp.name), snapshot_delay=0.05)
        self.previous = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'})
        self.project = self.store.create_project('shared')
        self.pid = self.project['id']
        image = io.BytesIO()
        Image.new('RGB', (8, 8), (30, 140, 220)).save(image, format='PNG')
        self.original = image.getvalue()
        self.media = self.store.import_media(self.pid, self.original, 'shared.png', 'image/png',
                                             {'type': 'online_generation', 'name': 'shared.png'})
        self.aid = self.media['asset_id']
        outputs = [{'asset_id': self.aid, 'kind': 'image', 'name': f'output-{i}.png'} for i in range(3)]
        self.store.apply_mutations(self.pid, [
            {'op': 'upsert_node', 'node': {'id': 'node', 'type': 'smart-image', 'images': copy.deepcopy(outputs)}},
            {'op': 'set_canvas_meta', 'history': [{'id': 'log', 'status': 'succeeded',
                'outputs': copy.deepcopy(outputs), 'result_node_id': 'node'}]},
        ], base_revision=0)
        self.ids = [item['output_id'] for item in self.store.canvas(self.pid)['history'][0]['outputs']]
        self.url = f'/api/project-library/{self.pid}/trash/media/{self.aid}/preview'
        self.project_dir = Path(self.temp.name) / self.pid

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous
        self.temp.cleanup()

    def delete(self, number, revision):
        return self.store.delete_generated_original(self.pid, self.aid, base_revision=revision,
            history_id='log', output_id=self.ids[number], output_index=0)['deleted']

    def assert_readable(self):
        for view in ('preview', 'original'):
            with self.subTest(view=view):
                response = self.client.get(self.url, params={'view': view})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, self.original)
                self.assertEqual(response.headers['x-canvas-media-view'], 'original')
                self.assertIn('image/png', response.headers['content-type'])

    def test_multiple_records_restore_and_purge(self):
        first = self.delete(0, 1)
        self.assertTrue(first['media_retained'])
        self.assertEqual(first['media_filename'], '')
        self.assert_readable()
        download = self.client.get('/api/download-output', params={'url': self.url + '?view=original'})
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, self.original)
        second = self.delete(1, 2)
        self.assertTrue(second['media_retained'])
        self.assertEqual(len(self.store.list_deleted_media(self.pid)), 2)
        self.assert_readable()
        third = self.delete(2, 3)
        self.assertFalse(third['media_retained'])
        # The final occurrence owns the moved original; older entries share its asset URL.
        self.assert_readable()
        self.store.restore_deleted_original(self.pid, self.aid, base_revision=4, deletion_id=first['deletion_id'])
        self.assert_readable()
        self.store.restore_deleted_original(self.pid, self.aid, base_revision=5, deletion_id=second['deletion_id'])
        self.assert_readable()
        self.assertEqual(self.store.purge_deleted_media(self.pid, self.aid), 1)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f'/api/project-library/{self.pid}/media/{self.aid}').content, self.original)

    def test_purge_retained_does_not_remove_live_shared_original(self):
        self.delete(0, 1)
        self.assert_readable()
        self.assertEqual(self.store.purge_deleted_media(self.pid, self.aid), 1)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f'/api/project-library/{self.pid}/media/{self.aid}').content, self.original)

    def test_purge_shared_asset_removes_all_deleted_occurrences(self):
        self.delete(0, 1)
        self.delete(1, 2)
        self.assertEqual(len(self.store.list_deleted_media(self.pid)), 2)
        self.assertEqual(self.store.purge_deleted_media(self.pid, self.aid), 2)
        self.assertEqual(self.store.list_deleted_media(self.pid), [])
        self.assertEqual(self.client.get(f'/api/project-library/{self.pid}/media/{self.aid}').content, self.original)

    def test_missing_live_original_is_404_for_both_views(self):
        self.delete(0, 1)
        (self.project_dir / 'media' / self.media['filename']).unlink()
        for view in ('preview', 'original'):
            self.assertEqual(self.client.get(self.url, params={'view': view}).status_code, 404)

    def test_other_assets_trash_original_is_not_used(self):
        self.delete(0, 1)
        (self.project_dir / 'media' / self.media['filename']).unlink()
        index_path = self.project_dir / 'trash' / 'index.json'
        data = json.loads(index_path.read_text(encoding='utf-8'))
        trash_media = self.project_dir / 'trash' / 'media'
        trash_media.mkdir(parents=True, exist_ok=True)
        (trash_media / 'other.png').write_bytes(b'other-asset')
        data['items'].append({'asset_id': 'other', 'media_filename': 'other.png',
                              'expires_at': 4102444800000})
        index_path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_owner_fallback_with_stale_live_index_and_view_order(self):
        self.delete(0, 1)
        self.delete(1, 2)
        live_path = self.project_dir / 'media' / 'index.json'
        live_index = json.loads(live_path.read_text(encoding='utf-8'))
        self.delete(2, 3)
        # Simulate an interrupted move: metadata still exists, but the file moved.
        live_path.write_text(json.dumps(live_index), encoding='utf-8')
        trash_path = self.project_dir / 'trash' / 'index.json'
        trash = json.loads(trash_path.read_text(encoding='utf-8'))
        owner = next(item for item in trash['items'] if not item['media_retained'])
        preview_dir = self.project_dir / 'trash' / 'previews'
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview = preview_dir / 'owner.png'
        preview.write_bytes(self.original + b'preview')
        owner['preview_filenames'] = [preview.name]
        trash_path.write_text(json.dumps(trash), encoding='utf-8')
        response = self.client.get(self.url, params={'view': 'preview'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, preview.read_bytes())
        self.assertEqual(response.headers['x-canvas-media-view'], 'preview')
        response = self.client.get(self.url, params={'view': 'original'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.original)
        self.assertEqual(response.headers['x-canvas-media-view'], 'original')
        (self.project_dir / 'trash' / 'media' / owner['media_filename']).unlink()
        response = self.client.get(self.url, params={'view': 'original'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, preview.read_bytes())
        self.assertEqual(response.headers['x-canvas-media-view'], 'preview')

    def test_owner_preview_traversal_cannot_escape_project(self):
        self.delete(0, 1)
        self.delete(1, 2)
        self.delete(2, 3)
        trash_path = self.project_dir / 'trash' / 'index.json'
        trash = json.loads(trash_path.read_text(encoding='utf-8'))
        owner = next(item for item in trash['items'] if not item['media_retained'])
        owner['preview_filenames'] = ['../../../outside.png']
        trash_path.write_text(json.dumps(trash), encoding='utf-8')
        (Path(self.temp.name) / 'outside.png').write_bytes(b'outside-secret')
        response = self.client.get(self.url, params={'view': 'preview'})
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'outside-secret', response.content)

    def test_live_index_traversal_cannot_escape_project(self):
        self.delete(0, 1)
        index_path = self.project_dir / 'media' / 'index.json'
        data = json.loads(index_path.read_text(encoding='utf-8'))
        data['items'][self.aid]['filename'] = '../../outside.png'
        index_path.write_text(json.dumps(data), encoding='utf-8')
        (Path(self.temp.name) / 'outside.png').write_bytes(b'outside-secret')
        with self.assertRaises(ProjectStoreError):
            self.store.trash_media_preview_path(self.pid, self.aid)
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'outside-secret', response.content)


if __name__ == '__main__':
    unittest.main()
