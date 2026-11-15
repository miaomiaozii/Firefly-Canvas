# -*- coding: utf-8 -*-
"""#366 仓库卫生守卫：日志、用户数据、根目录杂项不再进 git。

问 git 要答案，不去扫文件系统：质量门的隔离工作区就是
``git ls-files --cached --others --exclude-standard`` 的产物
（tools/quality_gate.py 的 create_execution_workspace），所以「入库了什么」
直接决定门里能看到什么。历史上被误跟踪的 8 个 .log 和 data/asset_library.json
就是这么混进门的（checkout 还会用仓里那份盖掉用户真实的素材索引）。
"""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(*args):
    proc = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout.splitlines()


try:
    _TRACKED = _git("ls-files")
except RuntimeError:  # 拿不到 git（极少见）就别把守卫当成失败
    _TRACKED = None


@unittest.skipIf(_TRACKED is None, "这个检出里 git 不可用")
class RepoHygieneTests(unittest.TestCase):
    """票 #366 的验收：日志与用户数据不入库，根目录没有杂项。"""

    def test_no_log_files_are_tracked(self):
        logs = [p for p in _TRACKED if p.endswith(".log")]
        self.assertEqual(
            logs, [], "日志不入库（历史误跟踪：.pipeline/*.log、.qa/*.log）：\n" + "\n".join(logs)
        )

    def test_user_data_is_not_tracked(self):
        tracked = set(_TRACKED)
        # 唯一例外：app/data/model_specs.json 是随代码分发的模型参数表，不是用户数据。
        for path in (
            "data/asset_library.json",
            "data/projects.json",
            "data/api_providers.json",
            "data/global_settings.json",
            "data/prompt_libraries.json",
            "history.json",
            "paseo.json",
        ):
            self.assertNotIn(path, tracked, f"{path} 是用户数据，不该被跟踪")

    def test_asset_library_index_is_ignored_not_just_untracked(self):
        """只 git rm --cached 不够：不写 ignore，下次 add . 又回来。"""
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "data/asset_library.json"],
            cwd=str(ROOT),
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, "data/asset_library.json 要在 .gitignore 里")

    def test_pipeline_and_qa_logs_are_ignored(self):
        # 三个都要钉：.pipeline 根下的、.pipeline 子目录里的（archive-m4）、.qa 下的。
        # 踩过一次：写成 `.pipeline/*.log` 时子目录那份没盖住，被 `git add -A` 又拉回了索引。
        for pattern in (
            ".pipeline/full-213554.log",
            ".pipeline/archive-m4/regression-round2.log",
            ".qa/e2e_run_075004.log",
        ):
            proc = subprocess.run(
                ["git", "check-ignore", "-q", pattern], cwd=str(ROOT), capture_output=True
            )
            self.assertEqual(proc.returncode, 0, f"{pattern} 要在 .gitignore 里")

    def test_root_has_no_stray_or_reserved_names(self):
        """`nul` 是 Windows 保留设备名（普通工具删不掉，还会破坏 grep），`~` 是 worker
        没展开 `~/.cache/...` 造出来的字面目录。两个都不该出现在仓库根目录。"""
        entries = set(os.listdir(ROOT))
        for stray in ("nul", "~", "paseo.json", "TODO-FIX.md"):
            self.assertNotIn(stray, entries, f"仓库根目录不该有 {stray}")

    def test_archived_docs_survive_the_move(self):
        """归档是挪位置不是删除：这些文档还在库里，只是搬到 docs/archive/。"""
        tracked = set(_TRACKED)
        for name in (
            "TODO-FIX.md",
            "HANDOVER-20260802.md",
            "HANDOFF-20260822.md",
            "code-review-phase2.md",
            "code-review-phase2-v2.md",
            "fix-summary-phase2.md",
            "generation-flow-review.md",
            "test-report.md",
            "issue197_recovery_audit.md",
        ):
            self.assertIn(f"docs/archive/{name}", tracked, f"docs/archive/{name} 应该还在库里")

    def test_no_doc_still_references_a_pre_archive_path(self):
        """搬完要把引用改掉，否则文档里全是指向不存在路径的死链。"""
        moved = (
            "TODO-FIX.md",
            "HANDOVER-20260802.md",
            "HANDOFF-20260822.md",
            "code-review-phase2",
            "fix-summary-phase2.md",
            "generation-flow-review.md",
            "issue197_recovery_audit.md",
        )
        hits = []
        for md in ROOT.rglob("*.md"):
            rel = md.relative_to(ROOT).as_posix()
            if rel.startswith(".local/") or rel.startswith("tmp/") or rel.startswith(".qa/"):
                continue
            if rel.startswith("docs/archive/"):
                continue  # 归档里的历史文本不去改
            for lineno, line in enumerate(md.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for name in moved:
                    for bad in (f"docs/{name}", f"`{name}`"):
                        if bad in line:
                            hits.append(f"  {rel}:{lineno}: {line.strip()[:90]}")
        self.assertEqual(hits, [], "仍有文档指向归档前的路径：\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
