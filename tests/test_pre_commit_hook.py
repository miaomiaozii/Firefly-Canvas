"""#370：提交前 hook 的安装与拦截行为。

这些测试不碰真仓库的配置：全部在临时 git 仓库里跑，用假的
`tools/quality_gate.py`（立刻 exit 1 / exit 0）代替真门，验的是
「hook 装上了、git 真的会调它、红了的门真的能把提交挡下来」。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.install_git_hooks import (
    HOOKS_DIR,
    check,
    configured_hooks_path,
    install,
    uninstall,
)

ROOT = Path(__file__).resolve().parents[1]
HOOK_SOURCE = ROOT / "tools" / "hooks" / "pre-commit"
INSTALLER = ROOT / "tools" / "install_git_hooks.py"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        # 本机默认编码是 GBK，hook 里的中文会让 text=True 直接炸；git/hook 输出都是 UTF-8。
        encoding="utf-8",
        errors="replace",
        env={
            **{k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")},
            "GIT_CONFIG_NOSYSTEM": "1",
            # 别把真仓库的 PATH 注入带进来（会把真 hook 挂到临时克隆上）。
            "GIT_CONFIG_GLOBAL": str(repo / "global.gitconfig"),
            "HOME": str(repo),
            "XDG_CONFIG_HOME": str(repo),
        },
    )


def shell_run(shell: str, script: Path, cwd: Path, extra_env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shell, str(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **extra_env},
    )


class PreCommitHookFileTests(unittest.TestCase):
    def test_hook_file_lives_in_the_repo_and_runs_the_fast_mode(self):
        self.assertTrue(HOOK_SOURCE.is_file(), f"缺少 {HOOK_SOURCE}")
        text = HOOK_SOURCE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh"), "hook 必须有 sh shebang（Git for Windows 用自带的 sh 跑）")
        self.assertIn("quality_gate.py fast", text)
        # 必须留一条明确的一次性绕过口子，否则紧急提交只能改代码绕。
        self.assertIn("FIREFLY_SKIP_PRECOMMIT", text)

    def test_installer_is_wired_and_documented(self):
        self.assertTrue(INSTALLER.is_file())
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("core.hooksPath", text)
        # 一键安装：不能要求开发者手工 git config（票面约束）。
        self.assertNotIn("请手工", text)


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-hook-test-")
        self.repo = Path(self.temp_dir.name) / "clone"
        self.repo.mkdir()
        result = git(self.repo, "init", "-q")
        self.assertEqual(result.returncode, 0, result.stderr)
        for key, value in (("user.name", "hook test"), ("user.email", "hook@test.invalid")):
            git(self.repo, "config", key, value)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _wire_repo_like_the_real_one(self, gate_exit_code: int) -> None:
        (self.repo / HOOKS_DIR).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HOOK_SOURCE, self.repo / HOOKS_DIR / "pre-commit")
        (self.repo / "tools").mkdir(exist_ok=True)
        # 假门：立刻按指定码退出，并留一行可断言的标记。
        (self.repo / "tools" / "quality_gate.py").write_text(
            "import sys\n"
            # 把收到的参数记下来：hook 必须用位置参数 `fast`（门没有 --mode）。
            "open('gate_args.txt', 'w', encoding='utf-8').write(' '.join(sys.argv[1:]))\n"
            "print('fake quality gate ran', file=sys.stderr)\n"
            f"sys.exit({gate_exit_code})\n",
            encoding="utf-8",
        )

    def test_install_points_hooks_path_at_the_repo_hooks(self):
        # 真克隆里 tools/hooks 跟着代码走，所以这里先把它摆好（不带假门）。
        (self.repo / HOOKS_DIR).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HOOK_SOURCE, self.repo / HOOKS_DIR / "pre-commit")
        self.assertEqual(configured_hooks_path(self.repo), None, "新克隆不该已有 hooksPath")
        self.assertEqual(check(self.repo), 1, "没装的时候 --check 必须红")
        self.assertEqual(install(self.repo), 0)
        self.assertEqual(configured_hooks_path(self.repo), HOOKS_DIR)
        self.assertEqual(check(self.repo), 0, "装完 --check 必须绿")
        # 幂等：重复安装不报错、不叠加配置。
        self.assertEqual(install(self.repo), 0)
        listed = git(self.repo, "config", "--get-all", "core.hooksPath")
        self.assertEqual(listed.stdout.split(), [HOOKS_DIR])

    def test_installer_refuses_a_repo_without_the_hook_files(self):
        self.assertEqual(install(self.repo), 2, "缺 tools/hooks/pre-commit 时不能只改配置")
        self.assertEqual(configured_hooks_path(self.repo), None)

    def test_uninstall_restores_git_default(self):
        install(self.repo)
        self.assertEqual(uninstall(self.repo), 0)
        self.assertEqual(configured_hooks_path(self.repo), None)

    def test_commit_is_blocked_when_the_gate_fails(self):
        # 票面验收：按文档安装 hook 后，一个会让 fast 档变红的改动提交不下去。
        self._wire_repo_like_the_real_one(gate_exit_code=1)
        self.assertEqual(install(self.repo), 0)
        (self.repo / "note.txt").write_text("would turn fast red\n", encoding="utf-8")
        self.assertEqual(git(self.repo, "add", "note.txt").returncode, 0)
        result = git(self.repo, "commit", "-m", "should be blocked")
        self.assertNotEqual(result.returncode, 0, "门红时必须挡住提交")
        self.assertIn("fake quality gate ran", result.stderr + result.stdout)
        self.assertEqual(
            (self.repo / "gate_args.txt").read_text(encoding="utf-8"),
            "fast",
            "hook 调门的参数必须是位置参数 fast",
        )
        log = git(self.repo, "log", "--oneline")
        self.assertNotEqual(log.returncode, 0, "仓库里不该多出这条提交")

    def test_commit_goes_through_when_the_gate_passes(self):
        self._wire_repo_like_the_real_one(gate_exit_code=0)
        self.assertEqual(install(self.repo), 0)
        (self.repo / "note.txt").write_text("fine\n", encoding="utf-8")
        self.assertEqual(git(self.repo, "add", "note.txt").returncode, 0)
        result = git(self.repo, "commit", "-m", "gate green")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fake quality gate ran", result.stderr + result.stdout)

    def test_skip_escape_hatch_skips_the_gate_for_one_commit(self):
        # 绕过口子只在显式设了变量的那一次生效（不是永久关 hook）。
        self._wire_repo_like_the_real_one(gate_exit_code=1)
        self.assertEqual(install(self.repo), 0)
        (self.repo / "note.txt").write_text("urgent\n", encoding="utf-8")
        self.assertEqual(git(self.repo, "add", "note.txt").returncode, 0)
        blocked = git(self.repo, "commit", "-m", "no escape")
        self.assertNotEqual(blocked.returncode, 0, "不设变量时照常挡")
        shell = shutil.which("sh") or shutil.which("bash")
        if shell is None:
            self.skipTest("本机没有 sh/bash，没法直接调 hook 验绕过口子")
        run = shell_run(shell, self.repo / HOOKS_DIR / "pre-commit", self.repo, {"FIREFLY_SKIP_PRECOMMIT": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("已跳过质量门", run.stderr)
        self.assertNotIn("fake quality gate ran", run.stderr)


if __name__ == "__main__":
    unittest.main()
