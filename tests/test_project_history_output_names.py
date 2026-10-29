"""#224 生成历史输出必须携带 name。

接缝：`app/routers/api_project_library.py::_history_output_entries`（本次抽出的小函数）。
archive_project_generated_image 返回的 item 本来就有 name（app/services/generation.py:1683+
的 `node_image = {... 'name': record.get('name') or filename ...}`），但写进
history_record['outputs'] 时被丢掉，星标面板只能退回裸 64 位 asset_id。
"""
from __future__ import annotations

import unittest

from app.routers.api_project_library import _history_output_entries


class HistoryOutputNameTests(unittest.TestCase):
    def test_name_is_preserved_from_generated_item(self):
        entries = _history_output_entries(
            [{'asset_id': 'a' * 64, 'kind': 'image', 'name': '生成结果-01.png'}],
            'image',
        )
        self.assertEqual(entries, [{'asset_id': 'a' * 64, 'kind': 'image', 'name': '生成结果-01.png'}])

    def test_missing_asset_id_is_skipped(self):
        self.assertEqual(_history_output_entries([{'name': '无 id'}], 'image'), [])

    def test_blank_name_omitted_instead_of_empty_string(self):
        """没有名字就干脆不带 name 键，前端据此判定「确实没名字」才退回 asset_id。"""
        entries = _history_output_entries([{'asset_id': 'x1', 'kind': 'image', 'name': '   '}], 'image')
        self.assertNotIn('name', entries[0])

    def test_kind_defaults_to_item_kind(self):
        entries = _history_output_entries([{'asset_id': 'v1', 'kind': 'video', 'name': '片段.mp4'}])
        self.assertEqual(entries[0]['kind'], 'video')
        self.assertEqual(entries[0]['name'], '片段.mp4')

    def test_long_name_is_capped(self):
        entries = _history_output_entries([{'asset_id': 'x1', 'kind': 'image', 'name': '长' * 400}], 'image')
        self.assertEqual(len(entries[0]['name']), 160)

    def test_non_dict_items_are_ignored(self):
        self.assertEqual(_history_output_entries(['oops', None, {'asset_id': 'ok'}], 'image'),
                         [{'asset_id': 'ok', 'kind': 'image'}])


if __name__ == '__main__':
    unittest.main()
