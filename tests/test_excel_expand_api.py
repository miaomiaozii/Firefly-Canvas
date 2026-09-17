"""Issue #108：Excel 展开 API 返回 bounds 的测试。"""

import pytest
from app.routers.api_excel_expand import _calculate_bounds


class TestCalculateBounds:
    """测试 _calculate_bounds 函数。"""

    def test_empty_operations_returns_none(self):
        assert _calculate_bounds([]) is None

    def test_no_create_node_ops_returns_none(self):
        ops = [
            {"type": "create_edge", "from": "a", "to": "b"},
        ]
        assert _calculate_bounds(ops) is None

    def test_single_node_bounds(self):
        ops = [
            {"type": "create_node", "node_id": "n1", "x": 100, "y": 200, "width": 300, "height": 150},
        ]
        bounds = _calculate_bounds(ops)
        assert bounds == {"min_x": 100, "min_y": 200, "max_x": 400, "max_y": 350}

    def test_multiple_nodes_bounds(self):
        ops = [
            {"type": "create_node", "node_id": "n1", "x": 100, "y": 200, "width": 300, "height": 150},
            {"type": "create_node", "node_id": "n2", "x": 500, "y": 400, "width": 200, "height": 100},
            {"type": "create_edge", "from": "n1", "to": "n2"},
        ]
        bounds = _calculate_bounds(ops)
        assert bounds == {"min_x": 100, "min_y": 200, "max_x": 700, "max_y": 500}

    def test_bounds_with_missing_dimensions(self):
        ops = [
            {"type": "create_node", "node_id": "n1", "x": 100, "y": 200},
        ]
        bounds = _calculate_bounds(ops)
        assert bounds == {"min_x": 100, "min_y": 200, "max_x": 100, "max_y": 200}

    def test_bounds_with_negative_coordinates(self):
        ops = [
            {"type": "create_node", "node_id": "n1", "x": -100, "y": -200, "width": 300, "height": 150},
        ]
        bounds = _calculate_bounds(ops)
        assert bounds == {"min_x": -100, "min_y": -200, "max_x": 200, "max_y": -50}
