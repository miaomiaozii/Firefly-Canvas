# -*- coding: utf-8 -*-
"""tools/doc_link_check.py fixture 测试（Issue #261）。

tmp 目录造一个迷你仓库：死链（文件不存在）+ 超行号 + 正常样例 + 裸文件名
多候选 + 相对 .md 链接 + 外部/锚点链接（应跳过），断言检出数与原因分类。
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.doc_link_check import check_repo, main  # noqa: E402


def _make_repo(root: Path) -> None:
    (root / "static" / "js" / "i18n").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / ".pipeline").mkdir()
    # 20 行；裸文件名 app.js 有两个候选（20 行 / 30 行）
    (root / "static" / "js" / "app.js").write_text("line\n" * 20, encoding="utf-8")
    (root / "static" / "js" / "i18n" / "app.js").write_text("line\n" * 30, encoding="utf-8")
    (root / "firefly_projects.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (root / "docs" / "other.md").write_text("# other\n", encoding="utf-8")
    (root / ".pipeline" / "plan.md").write_text("see `static/js/app.js:3`\n", encoding="utf-8")
    (root / "CONTEXT.md").write_text(
        "ok `static/js/app.js:10`\n"
        "ok edge `firefly_projects.py:50`\n"
        "ok bare `app.js:25`\n"
        "dead `missing.js:5`\n"
        "over `static/js/app.js:99`\n"
        "over bare `app.js:31`\n"
        "link [ok](docs/other.md) [anchor](docs/other.md#sec) [dead](docs/nope.md)\n"
        "skip [ext](https://example.com/a.md) [toc](#top)\n",
        encoding="utf-8",
    )


class DocLinkCheckFixtureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="doc-link-check-test-")
        self.root = Path(self._tmp.name)
        _make_repo(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_dead_line_number_and_normal_samples(self):
        findings, refs_checked = check_repo(self.root)
        self.assertEqual(refs_checked, 10)  # 7 code-ref + 3 在范围内 md 链接（ext/anchor 不计）
        by_reason = {}
        for finding in findings:
            by_reason[finding.reason] = by_reason.get(finding.reason, 0) + 1
        self.assertEqual(by_reason, {"missing": 2, "line-out-of-range": 2})
        pairs = {(f.ref, f.reason) for f in findings}
        self.assertIn(("missing.js:5", "missing"), pairs)
        self.assertIn(("docs/nope.md", "missing"), pairs)
        self.assertIn(("static/js/app.js:99", "line-out-of-range"), pairs)
        self.assertIn(("app.js:31", "line-out-of-range"), pairs)
        kinds = {(f.ref, f.kind) for f in findings}
        self.assertIn(("docs/nope.md", "md-link"), kinds)
        self.assertIn(("missing.js:5", "code-ref"), kinds)
        # 正常样例不得出现
        for ok_ref in ("static/js/app.js:10", "firefly_projects.py:50", "app.js:25", "docs/other.md"):
            self.assertNotIn(ok_ref, {f.ref for f in findings})
        # 去重：CONTEXT.md 里每条 ref 只出现一次
        self.assertEqual(len(findings), 4)

    def test_clean_repo_reports_zero(self):
        clean = Path(self._tmp.name) / "clean"
        (clean / "docs").mkdir(parents=True)
        (clean / "docs" / "ok.md").write_text("ref `docs/ok.md:1`\n", encoding="utf-8")
        findings, refs_checked = check_repo(clean)
        self.assertEqual(refs_checked, 1)
        self.assertEqual(findings, [])

    def test_exit_codes_report_and_advisory(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(self.root)]), 1)  # 有 missing -> 1
            self.assertIn("missing: 2", out.getvalue())
            self.assertIn("line-out-of-range: 2", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(self.root), "--advisory"]), 0)  # advisory 恒 0
            self.assertIn("advisory mode", out.getvalue())
        clean = Path(self._tmp.name) / "clean2"
        (clean / "docs").mkdir(parents=True)
        (clean / "docs" / "ok.md").write_text("ref `docs/ok.md:1`\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(clean)]), 0)  # 无失效 -> 0


if __name__ == "__main__":
    unittest.main()
