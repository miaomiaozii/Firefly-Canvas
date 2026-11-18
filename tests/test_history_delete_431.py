"""#431: real project-store deletion, persisted history, and restoration."""
import tempfile
import unittest
from pathlib import Path

from firefly_projects import ProjectStore, ProjectStoreError


class OriginalHistory431(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.pid = self.store.create_project('history regression')['id']
        self.a, self.b, self.c = [self.store.import_media(
            self.pid, name.encode(), name + '.png', 'image/png', {'type': 'online_generation'}
        )['asset_id'] for name in ('a', 'b', 'c')]
        def image(asset, output):
            return {'asset_id': asset, 'kind': 'image', 'output_id': output}
        self.a1, self.a2, self.bi, self.ci = (image(self.a, 'output-' + 'a' * 32), image(self.a, 'output-' + 'b' * 32), image(self.b, 'output-' + 'c' * 32), image(self.c, 'output-' + 'd' * 32))
        nodes = [{'id': name + '-node', 'type': 'smart-image', 'images': outputs} for name, outputs in
                 [('one', [self.a1]), ('multi', [self.a2, self.bi]), ('other', [self.ci])]]
        history = [{'id': name, 'status': 'succeeded', 'result_node_id': name + '-node', 'outputs': outputs} for name, outputs in
                   [('one', [self.a1]), ('multi', [self.a2, self.bi]), ('other', [self.ci])]]
        self.store.apply_mutations(self.pid, [*[{'op': 'upsert_node', 'node': node} for node in nodes],
            {'op': 'set_canvas_meta', 'history': history}], base_revision=0)

    def canvas(self):
        return self.store.open_project(self.pid)['canvas']

    def test_occurrence_delete_restore_preserves_other_occurrences_and_reload(self):
        before = self.canvas()
        deleted = self.store.delete_generated_original(self.pid, self.a, base_revision=before['revision'],
            history_id='one', output_id=self.a1['output_id'], output_index=0)
        history = {r['id']: r for r in deleted['canvas']['history']}
        self.assertEqual(history['one']['status'], 'original_deleted')
        self.assertEqual(history['one']['outputs'], [])
        self.assertEqual([x['asset_id'] for x in history['multi']['outputs']], [self.a, self.b])
        self.assertEqual(self.canvas()['history'], deleted['canvas']['history'])
        restored = self.store.restore_deleted_original(self.pid, self.a, base_revision=deleted['canvas']['revision'],
            deletion_id=deleted['deleted']['deletion_id'])
        self.assertEqual({r['id']: r for r in restored['canvas']['history']}['one']['outputs'][0]['output_id'], self.a1['output_id'])
        self.assertEqual(self.canvas()['history'], restored['canvas']['history'])

    def test_project_wide_delete_restore_and_failure(self):
        before = self.canvas()
        with self.assertRaises(ProjectStoreError):
            self.store.delete_generated_original_project_wide(self.pid, 'missing', base_revision=before['revision'])
        self.assertEqual(self.canvas()['history'], before['history'])
        deleted = self.store.delete_generated_original_project_wide(self.pid, self.a, base_revision=before['revision'])
        history = {r['id']: r for r in deleted['canvas']['history']}
        self.assertEqual(history['one']['status'], 'original_deleted')
        self.assertEqual([x['asset_id'] for x in history['multi']['outputs']], [self.b])
        self.assertEqual(history['other'], next(r for r in before['history'] if r['id'] == 'other'))
        self.assertEqual(self.canvas()['history'], deleted['canvas']['history'])
        restored = self.store.restore_deleted_original_project_wide(self.pid, self.a, base_revision=deleted['canvas']['revision'])
        history = {r['id']: r for r in restored['canvas']['history']}
        self.assertEqual(history['one']['status'], 'succeeded')
        self.assertEqual([x['asset_id'] for x in history['multi']['outputs']], [self.a, self.b])
        self.assertEqual(self.canvas()['history'], restored['canvas']['history'])


if __name__ == '__main__':
    unittest.main()
