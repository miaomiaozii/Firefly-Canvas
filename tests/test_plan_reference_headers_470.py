"""Intention column aliases preserve literal briefs and description-only image suggestions."""
from io import BytesIO
import json

from PIL import Image
import pytest

from excel_importer import SheetData
from plan_ir.agent_preview import project_workbook
from plan_ir.header_detector import (find_header_row, header_cell_looks_like_column_name,
                                     header_cell_score, map_headers)


ALIASES = ('示例图', '示意图', '意向图')


@pytest.mark.parametrize('label', ALIASES)
def test_intention_alias_is_recognized_in_all_header_stages(label):
    assert header_cell_score(label) == 2
    assert header_cell_looks_like_column_name(label)
    assert find_header_row([['序号', label]]) == 0
    assert map_headers(['序号', label]) == {'name': [0], 'operationRef': [1]}


@pytest.mark.parametrize('label', ALIASES)
def test_literal_row_three_has_two_description_images_without_extra_tasks(label):
    names = [f'主图{i}' for i in range(1, 9)] + [f'A+{i}' for i in range(1, 8)]
    rows = [['序号', '内容', '文案', label, '']]
    rows.extend([name, f'白底产品角度{i}', f'Copy {i}', '', '']
                for i, name in enumerate(names, 1))
    images = {}
    for rid, color in [('first', 'red'), ('second', 'blue')]:
        stream = BytesIO()
        Image.new('RGB', (8, 8), color).save(stream, format='PNG')
        images[rid] = stream.getvalue()
    sheet = SheetData(name='图片方案', raw_rows=rows, row_numbers=list(range(1, 17)), images=images,
                      image_anchors=[{'row': 3, 'col': 3, 'seq': 1, 'rid': 'first', 'path': 'maps/first.png'},
                                     {'row': 3, 'col': 4, 'seq': 1, 'rid': 'second', 'path': 'maps/second.png'}])
    preview = project_workbook({'sheets': {sheet.name: sheet}, 'cell_images': {}}, None)
    tasks = preview['tasks']
    assert len(tasks) == 15
    assert [task['source_row'] for task in tasks] == [f'图片方案!{row}' for row in range(2, 17)]
    task = tasks[1]
    assert task['source_row'] == '图片方案!3'
    assert task['name'] == '主图2'
    assert task['suggested_refs'] == [
        {'kind': 'workbook_image', 'name': 'first.png', 'source': label, 'enabled': False, 'usage': 'description_only'},
        {'kind': 'workbook_image', 'name': 'second.png', 'source': label, 'enabled': False, 'usage': 'description_only'},
    ]
    assert all(item['selected'] is False and item['refs'] == [] for item in tasks)
    assert all(not item['suggested_refs'] for i, item in enumerate(tasks) if i != 1)
    assert preview['excluded'] == []
    encoded = json.dumps(preview, ensure_ascii=False)
    assert 'maps/' not in encoded and 'base64' not in encoded


@pytest.mark.parametrize('label', ['参考', '效果图', '图片示例', '参考图', '参考图补充', '参考图片', '图片参考'])
def test_existing_reference_alias_mapping_is_unchanged(label):
    assert map_headers([label]) == {'operationRef': [0]}


@pytest.mark.parametrize('label', ['示例', '示意', '意向', '示例文案'])
def test_generic_example_words_do_not_become_image_columns(label):
    assert 'operationRef' not in map_headers([label])
