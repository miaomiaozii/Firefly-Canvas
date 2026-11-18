"""Deterministic fault injection, not a reproduction of the original flake (#435)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io


def denied(code=5):
    error = PermissionError("injected Windows error")
    error.winerror = code
    return error


class AtomicReplaceRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "state.json"
        self.target.write_text('{"old": true}', encoding="utf-8")

    def assert_clean(self):
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_transient_denial_reuses_staging_and_succeeds(self):
        real_replace = os.replace
        calls = []
        def replace(src, dst):
            calls.append((Path(src), Path(dst), Path(src).read_bytes()))
            if len(calls) < 3:
                raise denied()
            real_replace(src, dst)
        with patch.object(sys, "platform", "win32"), patch.object(firefly_json_io.os, "replace", side_effect=replace), patch.object(firefly_json_io.time, "sleep") as sleep:
            firefly_json_io.write_json_atomic(self.target, {"new": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"new": True})
        self.assert_clean()

    def test_persistent_denial_is_bounded_and_preserves_original(self):
        error = denied()
        with patch.object(sys, "platform", "win32"), patch.object(firefly_json_io.os, "replace", side_effect=error) as replace, patch.object(firefly_json_io.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                firefly_json_io.write_json_atomic(self.target, {"new": True})
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.01, 0.02, 0.04])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"old": true}')
        self.assert_clean()

    def test_other_error_or_non_windows_is_not_retried(self):
        for platform, error in [("win32", denied(32)), ("linux", denied()), ("win32", OSError("other"))]:
            with self.subTest(platform=platform, error=error), patch.object(sys, "platform", platform), patch.object(firefly_json_io.os, "replace", side_effect=error) as replace, patch.object(firefly_json_io.time, "sleep") as sleep:
                with self.assertRaises(OSError) as caught:
                    firefly_json_io.write_json_atomic(self.target, {"new": True})
                self.assertIs(caught.exception, error)
                self.assertEqual(replace.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(self.target.read_text(encoding="utf-8"), '{"old": true}')
                self.assert_clean()

    def test_backup_copy_retries_without_repeating_rotation(self):
        real_replace = os.replace
        attempts = []
        def replace(src, dst):
            attempts.append((Path(src), Path(dst)))
            if len(attempts) == 1:
                raise denied()
            real_replace(src, dst)
        with patch.object(sys, "platform", "win32"), patch.object(firefly_json_io.os, "replace", side_effect=replace), patch.object(firefly_json_io.time, "sleep"):
            firefly_json_io.write_json_atomic(self.target, {"new": True}, keep_backup=True)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(json.loads((self.root / "state.json.bak").read_text(encoding="utf-8")), {"old": True})
        self.assert_clean()
