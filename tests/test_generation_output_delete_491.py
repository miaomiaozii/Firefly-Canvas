"""A task's cumulative callbacks cannot republish its deleted partial output (#491)."""
import copy
from pathlib import Path
import tempfile
import unittest

from firefly_projects import ProjectStore


class GenerationOutputDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'projects'
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.addCleanup(lambda: self.store.close())
        self.pid = self.store.create_project('e2e-tabbit-generation-delete-491')['id']
        self.store.apply_mutations(self.pid, [
            {'op': 'upsert_node', 'node': {'id': name, 'type': 'smart-image', 'images': []}}
            for name in ('producer', 'copy')
        ], base_revision=0)
        self.assets = [self.store.import_media(
            self.pid, f'generated-{index}'.encode(), f'{index}.png', 'image/png',
            {'type': 'online_generation'},
        )['asset_id'] for index in range(2)]
        self.store.begin_image_generation(self.pid, 'producer', {
            'taskId': 'task', 'kind': 'image', 'count': 2, 'referenceAssetIds': [],
        }, base_revision=1)

    def canvas(self):
        return self.store.open_project(self.pid)['canvas']

    def complete(self, assets, *, partial=False, history_id='history', task_id='task'):
        outputs = [{'asset_id': asset, 'kind': 'image'} for asset in assets]
        return self.complete_outputs(outputs, partial=partial, history_id=history_id, task_id=task_id)

    def complete_outputs(self, outputs, *, partial=False, history_id='history', task_id='task'):
        record = {'id': history_id, 'task_id': task_id, 'result_node_id': 'producer',
                  'status': 'running' if partial else 'succeeded', 'outputs': copy.deepcopy(outputs)}
        result = self.store.complete_image_generation(
            self.pid, 'producer', outputs, record, task_id=task_id,
            require_pending=True, partial=partial,
        )
        return result, record

    @staticmethod
    def node(canvas, name='producer'):
        return next(node for node in canvas['nodes'] if node['id'] == name)

    def delete_partial(self, *, keep_copy=True):
        initial, _ = self.complete(self.assets[:1], partial=True)
        output = copy.deepcopy(initial['history'][0]['outputs'][0])
        if keep_copy:
            copied = self.node(initial, 'copy')
            copied_output = copy.deepcopy(output)
            copied_output['output_id'] = 'output-' + 'c' * 32
            copied['images'] = [copied_output]
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': copied}],
                                       base_revision=initial['revision'])
            self.copy_output = self.node(self.canvas(), 'copy')['images'][0]
            self.assertNotEqual(self.copy_output['output_id'], output['output_id'])
        deleted = self.store.delete_generated_original(
            self.pid, self.assets[0], base_revision=self.canvas()['revision'],
            history_id='history', output_id=output['output_id'], output_index=0,
        )
        self.assertEqual(self.node(deleted['canvas'])['images'], [])
        return output, deleted['deleted']['deletion_id']

    def test_cumulative_final_does_not_restore_deleted_partial_and_undo_survives(self):
        output, deletion_id = self.delete_partial()
        final, callback_record = self.complete(self.assets)
        self.assertEqual([item['asset_id'] for item in self.node(final)['images']], self.assets[1:])
        self.assertEqual([item['asset_id'] for item in final['history'][0]['outputs']], self.assets[1:])
        self.assertEqual(callback_record['outputs'], final['history'][0]['outputs'])
        self.assertEqual(self.node(final, 'copy')['images'], [self.copy_output])
        self.assertNotIn('pendingTasks', self.node(final))
        self.store.flush(self.pid)
        self.store.close()
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.assertEqual([item['asset_id'] for item in self.node(self.canvas())['images']], self.assets[1:])
        restored = self.store.restore_deleted_original(
            self.pid, self.assets[0], base_revision=self.canvas()['revision'], deletion_id=deletion_id,
        )['canvas']
        self.assertEqual({item['asset_id'] for item in self.node(restored)['images']}, set(self.assets))
        self.assertIn(output['output_id'], [item['output_id'] for item in self.node(restored)['images']])
        self.assertNotIn('pendingTasks', self.node(restored))
        self.assertEqual(restored['history'][0]['status'], 'succeeded')
        self.assertEqual(self.node(restored, 'copy')['images'], [self.copy_output])

    def test_callback_filters_deleted_media_before_validating_live_files(self):
        self.delete_partial(keep_copy=False)
        self.assertNotIn(self.assets[0], [item['asset_id'] for item in self.store.list_media(self.pid)])
        final, record = self.complete(self.assets)
        self.assertEqual([item['asset_id'] for item in self.node(final)['images']], self.assets[1:])
        self.assertEqual(record['outputs'], final['history'][0]['outputs'])

    def test_repeated_partial_then_final_cannot_republish_deleted_output(self):
        self.delete_partial()
        for partial in (True, False):
            result, record = self.complete(self.assets, partial=partial)
            self.assertEqual([item['asset_id'] for item in self.node(result)['images']], self.assets[1:])
            self.assertEqual([item['asset_id'] for item in record['outputs']], self.assets[1:])

    def test_final_with_only_deleted_output_still_finishes_task(self):
        self.delete_partial(keep_copy=False)
        result, record = self.complete(self.assets[:1])
        self.assertEqual(self.node(result)['images'], [])
        self.assertEqual(record['outputs'], [])
        self.assertNotIn('pendingTasks', self.node(result))
        self.assertEqual(self.node(result)['pending'], 0)

    def test_distinct_explicit_occurrences_of_same_original_survive_cumulative_callbacks(self):
        outputs = [{'asset_id': self.assets[0], 'kind': 'image', 'output_id': 'output-' + digit * 32}
                   for digit in ('a', 'b')]
        for output in outputs:
            self.complete_outputs([output], partial=True)
        for partial in (True, False):
            result, record = self.complete_outputs(outputs, partial=partial)
            self.assertEqual([item['output_id'] for item in self.node(result)['images']],
                             [item['output_id'] for item in outputs])
            self.assertEqual(record['outputs'], result['history'][0]['outputs'])
            self.assertEqual(len({item['output_order'] for item in record['outputs']}), 2)

    def test_same_task_fresh_same_bytes_occurrence_survives_delete_restart_and_undo(self):
        first = {'asset_id': self.assets[0], 'kind': 'image', 'output_id': 'output-' + 'a' * 32}
        initial, _ = self.complete_outputs([first], partial=True)
        copied = copy.deepcopy(initial['history'][0]['outputs'][0])
        copied['output_id'] = 'output-' + 'c' * 32
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'copy', 'type': 'smart-image', 'images': [copied],
        }}], base_revision=initial['revision'])
        deleted = self.store.delete_generated_original(
            self.pid, self.assets[0], base_revision=self.canvas()['revision'],
            history_id='history', output_id=first['output_id'], output_index=0,
        )
        fresh_asset = self.store.import_media(
            self.pid, b'generated-0', 'fresh.png', 'image/png', {'type': 'online_generation'},
        )['asset_id']
        self.assertEqual(fresh_asset, first['asset_id'])
        fresh = {'asset_id': fresh_asset, 'kind': 'image', 'output_id': 'output-' + 'b' * 32}
        partial, _ = self.complete_outputs([fresh], partial=True)
        self.assertEqual([item['output_id'] for item in self.node(partial)['images']], [fresh['output_id']])
        self.assertEqual(self.node(partial, 'copy')['images'], [copied])
        self.store.flush(self.pid)
        self.store.close()
        self.store = ProjectStore(self.root, snapshot_delay=0)
        for partial in (True, True, False):
            result, record = self.complete_outputs([first, fresh], partial=partial)
            self.assertEqual([item['output_id'] for item in self.node(result)['images']], [fresh['output_id']])
            self.assertEqual([item['output_id'] for item in record['outputs']], [fresh['output_id']])
        restored = self.store.restore_deleted_original(
            self.pid, first['asset_id'], base_revision=result['revision'],
            deletion_id=deleted['deleted']['deletion_id'],
        )['canvas']
        self.assertEqual({item['output_id'] for item in self.node(restored)['images']},
                         {first['output_id'], fresh['output_id']})
        self.assertEqual(len(restored['history'][0]['outputs']), 2)
        self.assertEqual(self.node(restored, 'copy')['images'], [copied])
        self.assertNotIn('pendingTasks', self.node(restored))

    def test_legacy_callbacks_deduplicate_after_store_restart(self):
        initial, _ = self.complete(self.assets[:1], partial=True)
        output_id = initial['history'][0]['outputs'][0]['output_id']
        self.store.flush(self.pid)
        self.store.close()
        self.store = ProjectStore(self.root, snapshot_delay=0)
        for partial in (True, False):
            result, _ = self.complete(self.assets[:1], partial=partial)
            self.assertEqual([item['output_id'] for item in self.node(result)['images']], [output_id])
            self.assertEqual(len(result['history'][0]['outputs']), 1)

    def test_deletion_does_not_suppress_another_history_using_same_original(self):
        output, _ = self.delete_partial()
        self.complete(self.assets[1:])
        self.store.begin_image_generation(self.pid, 'producer', {
            'taskId': 'independent-task', 'kind': 'image', 'count': 1, 'referenceAssetIds': [],
        }, base_revision=self.canvas()['revision'])
        result, record = self.complete(self.assets[:1], history_id='independent', task_id='independent-task')
        self.assertEqual([item['asset_id'] for item in self.node(result)['images']], self.assets[:1])
        self.assertNotEqual(record['outputs'][0]['output_id'], output['output_id'])
        self.assertEqual(self.node(result, 'copy')['images'], [self.copy_output])


if __name__ == '__main__':
    unittest.main()
