import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_quality_gate import assert_node_modules_unchanged, node_modules_snapshot


class NodeModulesGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="issue440-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.modules = self.root / "node_modules"

    def test_absence_and_unchanged_existing_tree(self):
        with assert_node_modules_unchanged(self, self.root):
            pass
        self.modules.mkdir()
        (self.modules / "existing").write_bytes(b"safe")
        with assert_node_modules_unchanged(self, self.root):
            pass

    def test_nested_workspace_dependencies_are_outside_the_root_guard(self):
        self.modules.mkdir()
        (self.modules / "existing").write_bytes(b"safe")
        nested = self.root / "nested-worktree" / "node_modules"
        with assert_node_modules_unchanged(self, self.root):
            nested.mkdir(parents=True)
            (nested / "typescript").write_bytes(b"installed in nested worktree")

    def test_creation_addition_removal_and_same_size_mutation(self):
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                self.modules.mkdir()
        existing = self.modules / "existing"
        existing.write_bytes(b"safe")
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                (self.modules / "added").write_bytes(b"new")
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                (self.modules / "added").unlink()
        original = existing.stat()
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                existing.write_bytes(b"evil")
                os.utime(existing, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                existing.unlink()
        with self.assertRaises(AssertionError):
            with assert_node_modules_unchanged(self, self.root):
                self.modules.rmdir()

    def test_synthetic_step_writing_to_root_is_caught(self):
        self.modules.mkdir()
        (self.modules / "untouched").write_bytes(b"user data")
        with self.assertRaisesRegex(AssertionError, "node_modules"):
            with assert_node_modules_unchanged(self, self.root):
                subprocess.run(
                    [sys.executable, "-c", "from pathlib import Path; Path('node_modules/synthetic').write_text('write')"],
                    cwd=self.root, check=True,
                )
        self.assertEqual((self.modules / "untouched").read_bytes(), b"user data")

    def test_symlink_target_contents_and_dangling_links(self):
        self.modules.mkdir()
        target = self.root / "target"
        target.mkdir()
        (target / "file").write_bytes(b"safe")
        link = self.modules / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        (target / "cycle").symlink_to(self.modules, target_is_directory=True)
        file_link = self.modules / "file-link"
        file_link.symlink_to(target / "file")
        before = node_modules_snapshot(self.root)
        (target / "file").write_bytes(b"evil")
        self.assertNotEqual(before, node_modules_snapshot(self.root))
        (target / "file").unlink()
        if os.name == "nt":
            (target / "cycle").rmdir()
        else:
            (target / "cycle").unlink()
        target.rmdir()
        dangling = node_modules_snapshot(self.root)
        self.assertNotEqual(before, dangling)
        self.assertEqual(dangling, node_modules_snapshot(self.root))
