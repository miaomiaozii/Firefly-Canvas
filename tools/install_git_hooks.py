"""#370：一键安装/检查提交前 hook。

做法是把 git 的 hooks 目录指到仓库里的 `tools/hooks`（`core.hooksPath`），
这样 hook 本身进版本库、跟着代码走，不用每人手工往 `.git/hooks/` 拷文件。

    python tools/install_git_hooks.py            # 安装
    python tools/install_git_hooks.py --check     # 只检查（没装则退出码 1）
    python tools/install_git_hooks.py --uninstall # 取消（恢复 git 默认）

`--check` 给测试和文档用；安装本身是幂等的，重复跑没有副作用。

注意：`core.hooksPath` 是仓库级配置，同一个仓库的一切 worktree 共用。在某个
worktree 里装了，主工作树和其他 worktree 也会跟着跑 hook。只想在单次提交上临时挂 hook，
用 `git -c core.hooksPath=tools/hooks commit ...`。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = "tools/hooks"
REQUIRED_HOOKS = ("pre-commit",)


def git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def configured_hooks_path(repo_root: Path) -> str | None:
    result = git(repo_root, "config", "--get", "core.hooksPath")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def missing_hook_files(repo_root: Path) -> list[str]:
    return [
        name
        for name in REQUIRED_HOOKS
        if not (repo_root / HOOKS_DIR / name).is_file()
    ]


def install(repo_root: Path) -> int:
    missing = missing_hook_files(repo_root)
    if missing:
        print(f"缺少 hook 文件：{missing}（应在 {HOOKS_DIR}/）", file=sys.stderr)
        return 2
    result = git(repo_root, "config", "core.hooksPath", HOOKS_DIR)
    if result.returncode != 0:
        print(f"git config core.hooksPath 失败：{result.stderr.strip()}", file=sys.stderr)
        return 2
    print(f"已安装：core.hooksPath = {HOOKS_DIR}（{repo_root}）")
    print("提交前会跑 `python tools/quality_gate.py fast`（模式是位置参数，不是 --mode）；")
    print("只跳过一次提交用 FIREFLY_SKIP_PRECOMMIT=1 git commit ...")
    return 0


def uninstall(repo_root: Path) -> int:
    git(repo_root, "config", "--unset-all", "core.hooksPath")
    print(f"已取消 core.hooksPath，git 回到默认 .git/hooks（{repo_root}）")
    return 0


def check(repo_root: Path) -> int:
    current = configured_hooks_path(repo_root)
    missing = missing_hook_files(repo_root)
    if current != HOOKS_DIR:
        print(f"未安装：core.hooksPath = {current!r}（应为 {HOOKS_DIR!r}）", file=sys.stderr)
        return 1
    if missing:
        print(f"hooksPath 已指到 {HOOKS_DIR}，但缺文件：{missing}", file=sys.stderr)
        return 1
    print(f"已安装：core.hooksPath = {HOOKS_DIR}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装/检查 #370 的提交前质量门 hook")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只检查，不改配置")
    group.add_argument("--uninstall", action="store_true", help="取消 core.hooksPath")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not (repo_root / ".git").exists():
        print(f"{repo_root} 不是 git 仓库根", file=sys.stderr)
        return 2
    if args.check:
        return check(repo_root)
    if args.uninstall:
        return uninstall(repo_root)
    return install(repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
