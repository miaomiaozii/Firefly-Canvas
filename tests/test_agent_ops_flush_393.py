import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
from firefly_projects import ProjectStore


class AtomicReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "synthetic.json"
        self.staging = self.root / ".synthetic.staging.tmp"

    def test_legacy_writer_success_failure_and_no_logging(self):
        with patch.object(firefly_json_io.logger, "handle") as log:
            self.assertIsNone(firefly_json_io.write_json_atomic(self.target, {"v": 1}))
            self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"v": 1})
            with patch.object(firefly_json_io, "_staging_path", return_value=self.staging):
                with patch.object(firefly_json_io, "_replace_staged", side_effect=OSError("replace")):
                    with self.assertRaisesRegex(OSError, "replace"):
                        firefly_json_io.write_json_atomic(self.target, {"v": 2})
            self.assertFalse(self.staging.exists())
            self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"v": 1})
            log.assert_not_called()

    def test_callback_once_after_replace_before_cleanup_and_callback_error(self):
        calls = []

        def replaced():
            calls.append(json.loads(self.target.read_text(encoding="utf-8")))
            self.assertFalse(self.staging.exists())
            raise RuntimeError("listener")

        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == self.staging:
                calls.append("cleanup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(firefly_json_io, "_staging_path", return_value=self.staging):
            with patch.object(Path, "unlink", unlink):
                with self.assertRaisesRegex(RuntimeError, "listener"):
                    firefly_json_io.write_json_atomic(self.target, {"v": 3}, on_replaced=replaced)
        self.assertEqual(calls, [{"v": 3}, "cleanup"])
        self.assertFalse(self.staging.exists())

    def test_before_replace_failure_does_not_notify(self):
        calls = []
        with patch.object(firefly_json_io, "_staging_path", return_value=self.staging):
            with patch.object(firefly_json_io, "_replace_staged", side_effect=OSError("replace")):
                with self.assertRaisesRegex(OSError, "replace"):
                    firefly_json_io.write_json_atomic(self.target, {"v": 1}, on_replaced=lambda: calls.append(1))
        self.assertEqual(calls, [])
        self.assertFalse(self.target.exists())
        self.assertFalse(self.staging.exists())

    def test_callback_and_cleanup_errors_keep_callback_as_context(self):
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == self.staging:
                raise OSError("cleanup")
            return original_unlink(path, *args, **kwargs)

        def replaced():
            raise RuntimeError("listener")

        with patch.object(firefly_json_io, "_staging_path", return_value=self.staging):
            with patch.object(Path, "unlink", unlink):
                with self.assertRaisesRegex(OSError, "cleanup") as error:
                    firefly_json_io.write_json_atomic(self.target, {"v": 5}, on_replaced=replaced)
        self.assertIsInstance(error.exception.__context__, RuntimeError)
        self.assertEqual(str(error.exception.__context__), "listener")

    def test_cleanup_error_after_replace_is_propagated(self):
        calls = []
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == self.staging:
                raise OSError("cleanup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(firefly_json_io, "_staging_path", return_value=self.staging):
            with patch.object(Path, "unlink", unlink):
                with self.assertRaisesRegex(OSError, "cleanup"):
                    firefly_json_io.write_json_atomic(self.target, {"v": 4}, on_replaced=lambda: calls.append(1))
        self.assertEqual(calls, [1])
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"v": 4})


class FlushObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project_id = self.store.create_project("synthetic")["id"]
        self.store.open_project(self.project_id)
        self.canvas_path = self.store.root / self.project_id / "canvas.json"
        self.store._canvases[self.project_id]["title"] = "new title"
        self.expected = self.store._project_canvas_for_persistence(self.store._canvases[self.project_id])

    def persisted(self):
        return json.loads(self.canvas_path.read_text(encoding="utf-8"))

    def test_subscriptions_are_isolated_and_results_unchanged(self):
        events = []

        def first(project_id, canvas):
            self.assertTrue(self.store._lock._is_owned())
            self.assertEqual(self.persisted(), self.expected)
            canvas["title"] = "mutated"
            events.append(project_id)

        def second(project_id, canvas):
            events.append((project_id, canvas))

        self.store.add_persisted_canvas_listener(first)
        self.store.add_persisted_canvas_listener(first)
        self.store.add_persisted_canvas_listener(second)
        result = self.store.flush(self.project_id)
        self.assertEqual(result, {"project_id": self.project_id, "revision": self.expected["revision"], "saved_at": self.expected["updated_at"]})
        self.assertEqual(events, [self.project_id, (self.project_id, self.expected)])
        self.assertEqual(self.persisted(), self.expected)
        self.assertEqual(self.store._canvases[self.project_id]["title"], "new title")
        self.store.remove_persisted_canvas_listener(first)
        self.store.flush(self.project_id)
        self.assertEqual(events[-1], (self.project_id, self.expected))
        self.assertEqual(len(events), 3)
        self.store.remove_persisted_canvas_listener(second)
        self.assertEqual(self.store.flush(self.project_id), result)
        self.assertEqual(len(events), 3)

    def test_listener_failure_propagates_and_later_flush_can_notify(self):
        events = []

        def listener(pid, canvas):
            events.append((pid, canvas))
            if len(events) == 1:
                raise RuntimeError("listener failed")

        self.store.add_persisted_canvas_listener(listener)
        with self.assertRaisesRegex(RuntimeError, "listener failed"):
            self.store.flush(self.project_id)
        self.assertEqual(self.persisted(), self.expected)
        self.store.flush(self.project_id)
        self.assertEqual(events, [(self.project_id, self.expected)] * 2)

    def check_listener_errors(self, errors):
        events = []
        staging = self.canvas_path.parent / ".canvas.listener-test.tmp"
        original_staging = firefly_json_io._staging_path
        original_unlink = Path.unlink

        def staging_path(target):
            return staging if target == self.canvas_path else original_staging(target)

        def unlink(path, *args, **kwargs):
            if path == staging:
                events.append("cleanup")
            return original_unlink(path, *args, **kwargs)

        def listener(index, error):
            def observe(pid, canvas):
                self.assertTrue(self.store._lock._is_owned())
                self.assertEqual(pid, self.project_id)
                self.assertEqual(canvas, self.expected)
                self.assertEqual(self.persisted(), self.expected)
                events.append(index)
                canvas["title"] = "listener mutation"
                if error is not None:
                    raise error
            return observe

        for index, error in enumerate(errors):
            self.store.add_persisted_canvas_listener(listener(index, error))

        with patch.object(firefly_json_io, "_staging_path", side_effect=staging_path):
            with patch.object(Path, "unlink", unlink):
                with self.assertRaises(BaseException) as raised:
                    self.store.flush(self.project_id)
        self.assertEqual(events, [*range(len(errors)), "cleanup"])
        self.assertFalse(staging.exists())
        self.assertEqual(self.persisted(), self.expected)
        failures = [error for error in errors if error is not None]
        if len(failures) == 1:
            self.assertIs(raised.exception, failures[0])
        else:
            group_type = ExceptionGroup if all(isinstance(error, Exception) for error in failures) else BaseExceptionGroup
            self.assertIsInstance(raised.exception, group_type)
            self.assertEqual(len(raised.exception.exceptions), len(failures))
            for actual, expected in zip(raised.exception.exceptions, failures):
                self.assertIs(actual, expected)

    def test_first_listener_error_does_not_prevent_later_observation(self):
        self.check_listener_errors([RuntimeError("first"), None])

    def test_multiple_listener_errors_are_all_propagated_after_observation(self):
        self.check_listener_errors([RuntimeError("first"), ValueError("second"), None])

    def test_base_exception_does_not_prevent_later_observation(self):
        self.check_listener_errors([KeyboardInterrupt("interrupt"), RuntimeError("second"), None])

    def test_before_replace_then_future_flush_observes_recovery(self):
        events = []
        self.store.add_persisted_canvas_listener(lambda pid, canvas: events.append((pid, canvas)))
        old = self.persisted()
        original = firefly_json_io._replace_staged

        def fail_canvas(staging, target):
            if target == self.canvas_path:
                raise OSError("canvas replace")
            return original(staging, target)

        with patch.object(firefly_json_io, "_replace_staged", side_effect=fail_canvas):
            with self.assertRaisesRegex(OSError, "canvas replace"):
                self.store.flush(self.project_id)
        self.assertEqual(events, [])
        self.assertEqual(self.persisted(), old)
        self.store.flush(self.project_id)
        self.assertEqual(events, [(self.project_id, self.expected)])
        self.assertEqual(self.persisted(), self.expected)

    def test_history_error_occurs_after_observation_and_next_flush_notifies(self):
        events = []
        self.store.add_persisted_canvas_listener(lambda pid, canvas: events.append((pid, canvas)))
        original = firefly_json_io._replace_staged

        def fail_history(staging, target):
            if target == self.canvas_path.parent / "history.json":
                raise OSError("history replace")
            return original(staging, target)

        with patch.object(firefly_json_io, "_replace_staged", side_effect=fail_history):
            with self.assertRaisesRegex(OSError, "history replace"):
                self.store.flush(self.project_id)
        self.assertEqual(events, [(self.project_id, self.expected)])
        self.assertEqual(self.persisted(), self.expected)
        self.store.flush(self.project_id)
        self.assertEqual(events, [(self.project_id, self.expected)] * 2)

    def test_cleanup_error_occurs_after_observation(self):
        events = []
        self.store.add_persisted_canvas_listener(lambda pid, canvas: events.append((pid, copy.deepcopy(canvas))))
        staging = self.canvas_path.parent / ".canvas.synthetic.tmp"
        original_staging = firefly_json_io._staging_path
        original_unlink = Path.unlink

        def staging_path(target):
            return staging if target == self.canvas_path else original_staging(target)

        def unlink(path, *args, **kwargs):
            if path == staging:
                raise OSError("canvas cleanup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(firefly_json_io, "_staging_path", side_effect=staging_path):
            with patch.object(Path, "unlink", unlink):
                with self.assertRaisesRegex(OSError, "canvas cleanup"):
                    self.store.flush(self.project_id)
        self.assertEqual(events, [(self.project_id, self.expected)])
        self.assertEqual(self.persisted(), self.expected)
        self.store.flush(self.project_id)
        self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
