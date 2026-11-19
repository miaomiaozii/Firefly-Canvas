import copy
import json
import unittest

from app.services.agent_ops_delta import apply_inverse, build_delta, touched_node_ids


def canvas(nodes=(), connections=(), **extra):
    return {"nodes": list(nodes), "connections": list(connections), **extra}


class AgentOpsDeltaTests(unittest.TestCase):
    def assert_inverse(self, before, after, current=None):
        delta = build_delta(before, after)
        original_before, original_after = copy.deepcopy(before), copy.deepcopy(after)
        source = after if current is None else current
        original_source = copy.deepcopy(source)
        restored, conflicts, already = apply_inverse(source, delta)
        self.assertEqual(conflicts, [])
        self.assertFalse(already)
        self.assertEqual(before, original_before)
        self.assertEqual(after, original_after)
        self.assertEqual(source, original_source)
        return restored, delta

    def test_create_nodes_and_edge(self):
        before = canvas()
        after = canvas([{"id": "b"}, {"id": "a"}], [{"id": "e", "from": "a", "to": "b"}])
        restored, delta = self.assert_inverse(before, after)
        self.assertEqual(restored, before)
        self.assertEqual(touched_node_ids(delta), ["a", "b"])
        self.assertEqual([(d["entity"], d["id"]) for d in delta],
                         [("node", "a"), ("node", "b"), ("connection", "e")])

    def test_delete_node_and_implicitly_removed_edges(self):
        node = {"id": "a", "type": "smart-image"}
        edge = {"fromNode": "a", "toNode": "b", "sourcePort": "out", "targetPort": "in"}
        before = canvas([node, {"id": "b"}], [edge])
        after = canvas([{"id": "b"}])
        restored, delta = self.assert_inverse(before, after)
        self.assertEqual({n["id"] for n in restored["nodes"]}, {"a", "b"})
        self.assertEqual(restored["connections"], [edge])
        self.assertEqual(delta[-1]["id"], "a->b:out:in")
        self.assertEqual(touched_node_ids(delta), ["a", "b"])

    def test_preserve_unrelated_node_edge_and_canvas_fields(self):
        before = canvas([{"id": "a", "x": 1, "label": "old"}],
                        [{"id": "e", "from": "a", "to": "b", "color": "red"}], title="old")
        after = canvas([{"id": "a", "x": 2, "label": "old"}],
                       [{"id": "e", "from": "a", "to": "b", "color": "blue"}], title="new")
        current = copy.deepcopy(after)
        current["nodes"][0]["label"] = "user"
        current["connections"][0]["weight"] = 5
        current["title"] = "user title"
        restored, _ = self.assert_inverse(before, after, current)
        self.assertEqual(restored["nodes"][0], {"id": "a", "x": 1, "label": "user"})
        self.assertEqual(restored["connections"][0]["color"], "red")
        self.assertEqual(restored["connections"][0]["weight"], 5)
        self.assertEqual(restored["title"], "user title")

    def test_nested_mapping_field_preservation_and_lists_atomic(self):
        before = canvas([{"id": "a", "meta": {"deep": {"x": 1, "keep": 4}}, "items": [1, 2]}])
        after = canvas([{"id": "a", "meta": {"deep": {"x": 2, "keep": 4}}, "items": [1, 3]}])
        current = copy.deepcopy(after)
        current["nodes"][0]["meta"]["deep"]["keep"] = 8
        restored, delta = self.assert_inverse(before, after, current)
        self.assertEqual(restored["nodes"][0]["meta"]["deep"], {"x": 1, "keep": 8})
        self.assertEqual(restored["nodes"][0]["items"], [1, 2])
        self.assertEqual([d["path"] for d in delta], [["items"], ["meta", "deep", "x"]])
        current["nodes"][0]["items"].append(4)
        result, conflicts, already = apply_inverse(current, delta)
        self.assertEqual(result, current)
        self.assertTrue(conflicts)
        self.assertFalse(already)

    def test_absence_is_distinct_from_null_and_false_from_zero(self):
        before = canvas([{"id": "a", "null_to_missing": None, "flag": False}])
        after = canvas([{"id": "a", "missing_to_null": None, "flag": 0}])
        restored, delta = self.assert_inverse(before, after)
        self.assertEqual(restored, before)
        changes = {d["path"][0]: d for d in delta}
        self.assertEqual(changes["missing_to_null"]["before"], {"present": False})
        self.assertEqual(changes["missing_to_null"]["after"], {"present": True, "value": None})
        self.assertEqual(changes["null_to_missing"]["before"], {"present": True, "value": None})
        current = copy.deepcopy(after)
        del current["nodes"][0]["missing_to_null"]
        restored, conflicts, already = apply_inverse(current, delta)
        self.assertFalse(conflicts)
        self.assertFalse(already)
        self.assertEqual(restored, before)

    def test_smart_group_items_and_new_node_undo_together(self):
        before = canvas([{"id": "g", "type": "smart-group", "items": ["old"], "w": 100}])
        after = canvas([{"id": "g", "type": "smart-group", "items": ["old", "n"], "w": 200}, {"id": "n"}])
        restored, delta = self.assert_inverse(before, after)
        self.assertEqual(restored, before)
        self.assertEqual(touched_node_ids(delta), ["g", "n"])

    def test_other_user_connection_blocks_created_node_deletion(self):
        before = canvas()
        after = canvas([{"id": "n"}])
        current = canvas([{"id": "n"}], [{"from": "n", "to": "other"}])
        result, conflicts, already = apply_inverse(current, build_delta(before, after))
        self.assertEqual(result, current)
        self.assertIsNot(result, current)
        self.assertEqual(conflicts[0]["node_id"], "n")
        self.assertIn("connection", conflicts[0]["reason"])
        self.assertFalse(already)

    def test_other_user_group_membership_blocks_created_node_deletion(self):
        before = canvas([{"id": "g", "type": "smart-group", "items": []}])
        after = canvas([{"id": "g", "type": "smart-group", "items": []}, {"id": "n"}])
        current = copy.deepcopy(after)
        current["nodes"][0]["items"].append("n")
        result, conflicts, already = apply_inverse(current, build_delta(before, after))
        self.assertEqual(result, current)
        self.assertIn("membership", conflicts[0]["reason"])
        self.assertFalse(already)

    def test_numeric_node_id_matches_smart_group_membership(self):
        before = canvas([{"id": "g", "type": "smart-group", "items": []}])
        after = canvas([{"id": "g", "type": "smart-group", "items": []}, {"id": 42}])
        current = copy.deepcopy(after)
        current["nodes"][0]["items"].append(42)
        result, conflicts, already = apply_inverse(current, build_delta(before, after))
        self.assertEqual(result, current)
        self.assertEqual(conflicts[0]["node_id"], "42")
        self.assertFalse(already)

    def test_group_with_null_items_has_no_membership(self):
        before = canvas([{"id": "g", "type": "smart-group", "items": None}])
        after = canvas([{"id": "g", "type": "smart-group", "items": None}, {"id": "n"}])
        result, _ = self.assert_inverse(before, after)
        self.assertEqual(result, before)

    def test_reference_conflict_rolls_back_unrelated_pending_inverse(self):
        before = canvas([{"id": "a", "x": 1}])
        after = canvas([{"id": "a", "x": 2}, {"id": "n"}])
        current = canvas([{"id": "a", "x": 2}, {"id": "n"}],
                         [{"id": "user", "from": "a", "to": "n"}])
        result, conflicts, already = apply_inverse(current, build_delta(before, after))
        self.assertEqual(result, current)
        self.assertEqual(result["nodes"][0]["x"], 2)
        self.assertEqual(conflicts[0]["node_id"], "n")
        self.assertFalse(already)

    def test_already_reverted_and_empty(self):
        before = canvas([{"id": "a", "x": 1}])
        after = canvas([{"id": "a", "x": 2}])
        delta = build_delta(before, after)
        result, conflicts, already = apply_inverse(before, delta)
        self.assertEqual(result, before)
        self.assertIsNot(result, before)
        self.assertEqual(conflicts, [])
        self.assertTrue(already)
        self.assertEqual(apply_inverse(before, []), (before, [], True))

    def test_partially_reverted_succeeds_but_not_already(self):
        before = canvas([{"id": "a", "x": 1, "y": 1}])
        after = canvas([{"id": "a", "x": 2, "y": 2}])
        current = canvas([{"id": "a", "x": 1, "y": 2}])
        restored, _ = self.assert_inverse(before, after, current)
        self.assertEqual(restored, before)

    def test_concurrent_field_conflict_is_all_or_none_across_entities(self):
        before = canvas([{"id": "a", "x": 1}, {"id": "b", "x": 1}])
        after = canvas([{"id": "a", "x": 2}, {"id": "b", "x": 2}])
        current = canvas([{"id": "a", "x": 2}, {"id": "b", "x": 3}])
        result, conflicts, already = apply_inverse(current, build_delta(before, after))
        self.assertEqual(result, current)
        self.assertEqual(conflicts[0]["node_id"], "b")
        self.assertFalse(already)

    def test_missing_parent_or_entity_conflicts_even_when_leaf_absent(self):
        before = canvas([{"id": "a", "meta": {}}])
        after = canvas([{"id": "a", "meta": {"x": 1}}])
        for current in (canvas([{"id": "a"}]), canvas([]), canvas([{"id": "a", "meta": None}])):
            with self.subTest(current=current):
                result, conflicts, already = apply_inverse(current, build_delta(before, after))
                self.assertEqual(result, current)
                self.assertTrue(conflicts)
                self.assertFalse(already)

    def test_concurrent_changes_to_created_or_deleted_entity_conflict(self):
        before = canvas()
        after = canvas([{"id": "n", "x": 1}])
        current = canvas([{"id": "n", "x": 1, "user": True}])
        for old, new in ((before, after), (after, before)):
            result, conflicts, already = apply_inverse(current, build_delta(old, new))
            self.assertEqual(result, current)
            self.assertTrue(conflicts)
            self.assertFalse(already)

    def test_reject_duplicate_or_missing_identity_in_all_inputs(self):
        bad = [canvas([{"id": "a"}, {"id": "a"}]), canvas([{"type": "prompt"}]),
               canvas([], [{"from": "a", "to": "b"}, {"source": "a", "target": "b"}]),
               canvas([], [{"id": "e"}, {"id": "e"}]), canvas([], [{"from": "a"}])]
        for invalid in bad:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    build_delta(invalid, canvas())
                with self.assertRaises(ValueError):
                    build_delta(canvas(), invalid)
                with self.assertRaises(ValueError):
                    apply_inverse(invalid, [])

    def test_edge_aliases_and_identity_are_store_compatible(self):
        edges = [{"sourceNodeId": "a", "targetNodeId": "b", "fromPort": "x", "toPort": "y"},
                 {"id": "explicit", "source": "c", "target": "d"}]
        delta = build_delta(canvas(), canvas([], edges))
        self.assertEqual([d["id"] for d in delta], ["a->b:x:y", "explicit"])
        self.assertEqual(touched_node_ids(delta), ["a", "b", "c", "d"])

    def test_empty_endpoint_alias_is_not_a_touched_node(self):
        edge = {"id": "e", "from": "a", "to": "b", "sourceNodeId": ""}
        delta = build_delta(canvas(), canvas([], [edge]))
        self.assertEqual(touched_node_ids(delta), ["a", "b"])

    def test_turn_deltas_must_be_undone_newest_first(self):
        first = canvas([{"id": "a", "x": 0}])
        second = canvas([{"id": "a", "x": 1}])
        third = canvas([{"id": "a", "x": 2}])
        first_delta = build_delta(first, second)
        second_delta = build_delta(second, third)
        unchanged, conflicts, _ = apply_inverse(third, first_delta)
        self.assertEqual(unchanged, third)
        self.assertTrue(conflicts)
        current, conflicts, _ = apply_inverse(third, second_delta)
        self.assertFalse(conflicts)
        current, conflicts, _ = apply_inverse(current, first_delta)
        self.assertFalse(conflicts)
        self.assertEqual(current, first)

    def test_explicit_edge_field_change_tracks_old_and_new_endpoints(self):
        before = canvas([], [{"id": "e", "from": "a", "to": "b", "style": {"color": "red"}}])
        after = canvas([], [{"id": "e", "from": "a", "to": "c", "style": {"color": "blue"}}])
        current = copy.deepcopy(after)
        current["connections"][0]["style"]["width"] = 4
        result, delta = self.assert_inverse(before, after, current)
        self.assertEqual(result["connections"][0]["to"], "b")
        self.assertEqual(result["connections"][0]["style"], {"color": "red", "width": 4})
        self.assertEqual(touched_node_ids(delta), ["a", "b", "c"])
        self.assertEqual([d["path"] for d in delta], [["style", "color"], ["to"]])

    def test_explicit_and_derived_edge_identity_collision_is_rejected(self):
        ambiguous = canvas([], [{"id": "a->b::"}, {"from": "a", "to": "b"}])
        with self.assertRaises(ValueError):
            build_delta(canvas(), ambiguous)

    def test_delta_is_deterministic_detached_and_never_stores_canvas(self):
        before = canvas([{"id": "z", "meta": {"k": 1, "unrelated": "secret"}}],
                        title="top level secret", format="firefly-canvas-v1")
        after = canvas([{"meta": {"unrelated": "secret", "k": 2}, "id": "z"}],
                       title="another secret", format="firefly-canvas-v1")
        delta = build_delta(before, after)
        self.assertEqual(delta, [{"entity": "node", "id": "z", "path": ["meta", "k"],
                                  "before": {"present": True, "value": 1},
                                  "after": {"present": True, "value": 2}}])
        self.assertNotIn("secret", json.dumps(delta))
        node = {"id": "new", "meta": {"x": [1]}}
        created = build_delta(canvas(), canvas([node]))
        node["meta"]["x"].append(2)
        self.assertEqual(created[0]["after"]["value"]["meta"]["x"], [1])
        result, _, _ = apply_inverse(canvas(), build_delta(canvas([node]), canvas()))
        result["nodes"][0]["meta"]["x"].append(3)
        self.assertEqual(node["meta"]["x"], [1, 2])


if __name__ == "__main__":
    unittest.main()
