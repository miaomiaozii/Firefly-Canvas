"""#184 节点类型常量一致性守卫。

后端 `app/constants.py` 与前端 `static/js/canvas-node-types.js` 必须登记同一组
NODE_TYPE_* 常量（名字与值都一致），且都等于 8 种节点类型的全集。
任何一方新增 / 修改 / 删除类型，本守卫都会变红。
"""
from __future__ import annotations

import re
import unittest

from app import constants
from tests.canvas_frontend_sources import CANVAS_SCRIPTS, ROOT

JS_NODE_TYPES = 'static/js/canvas-node-types.js'
DECL_RE = re.compile(r"(?:const|let|var)\s+(NODE_TYPE_[A-Z0-9_]+)\s*=\s*['\"]([^'\"]+)['\"]")

EXPECTED_NAMES = {
    'NODE_TYPE_PROMPT',
    'NODE_TYPE_IMAGE',
    'NODE_TYPE_AGENT',
    'NODE_TYPE_CONVERSATION',
    'NODE_TYPE_TASK',
    'NODE_TYPE_LOOP',
    'NODE_TYPE_GROUP',
    'NODE_TYPE_LOCAL_EDIT',
}
EXPECTED_VALUES = {
    'smart-prompt',
    'smart-image',
    'smart-agent',
    'smart-conversation',
    'agent-task',
    'smart-loop',
    'smart-group',
    'local-edit',
}


def _python_node_type_map() -> dict:
    return {
        name: getattr(constants, name)
        for name in dir(constants)
        if name.startswith('NODE_TYPE_') and isinstance(getattr(constants, name), str)
    }


def _javascript_node_type_map() -> dict:
    text = (ROOT / JS_NODE_TYPES).read_text(encoding='utf-8')
    return {m.group(1): m.group(2) for m in DECL_RE.finditer(text)}


class NodeTypeConstantsConsistencyTests(unittest.TestCase):
    def test_python_constants_cover_all_eight_types(self):
        pm = _python_node_type_map()
        self.assertEqual(
            set(pm), EXPECTED_NAMES,
            'app/constants.py NODE_TYPE_* name set is wrong: %r' % sorted(pm))
        self.assertEqual(
            set(pm.values()), EXPECTED_VALUES,
            'app/constants.py NODE_TYPE_* value set is wrong: %r' % sorted(pm.values()))

    def test_javascript_constants_cover_all_eight_types(self):
        jm = _javascript_node_type_map()
        self.assertEqual(
            set(jm), EXPECTED_NAMES,
            'canvas-node-types.js NODE_TYPE_* name set is wrong: %r' % sorted(jm))
        self.assertEqual(
            set(jm.values()), EXPECTED_VALUES,
            'canvas-node-types.js NODE_TYPE_* value set is wrong: %r' % sorted(jm.values()))

    def test_frontend_and_backend_type_maps_are_identical(self):
        pm = _python_node_type_map()
        jm = _javascript_node_type_map()
        self.assertEqual(
            set(pm.values()), set(jm.values()),
            'frontend/backend node-type sets differ: py=%r js=%r'
            % (sorted(pm.values()), sorted(jm.values())))
        self.assertEqual(
            pm, jm,
            'frontend/backend NODE_TYPE_* name->value maps differ: %r'
            % {k: (pm.get(k), jm.get(k)) for k in set(pm) ^ set(jm) | set(pm) & set(jm) if pm.get(k) != jm.get(k)})

    def test_javascript_module_is_registered_in_canvas_scripts(self):
        self.assertIn(
            'canvas-node-types.js', list(CANVAS_SCRIPTS),
            'canvas-node-types.js missing from tests/canvas_frontend_sources.py CANVAS_SCRIPTS')


if __name__ == '__main__':
    unittest.main()
