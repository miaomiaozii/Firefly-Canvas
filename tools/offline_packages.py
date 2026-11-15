# -*- coding: utf-8 -*-
"""#362 离线依赖包管理：核对 packages/ 是否齐全、按锁文件重新下载 wheel。

为什么需要它：`安装依赖.bat` 先试离线安装（`pip install --no-index --find-links=packages`），
但 `packages/` 里的 wheel 是手工放进去的——加一个依赖（#363 加 truststore）或换 Python 小版本
都没人重新下载，离线安装就在别人机器上炸掉（票面报的是 numpy/onnxruntime/pillow 装不上，
实测先炸的是 truststore 根本不在目录里）。这个工具把「锁文件 ↔ packages/」的对应关系变成可检查的。

用法:
  python tools/offline_packages.py check                # 核对 packages/ 覆盖 requirements.lock
  python tools/offline_packages.py download             # 按 requirements.lock 下载 wheel 进 packages/
  python tools/offline_packages.py check --dir tmp/pkg312   # 换目录核对（演练用）

check 退出码 0 = 齐全；1 = 有缺或 ABI 不对，并打印每一条原因。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"
PACKAGES = ROOT / "packages"

# 便携包与启动器都钉 Python 3.12（build_portable.py:21 PY_VER = '3.12.10'）。
# wheel 文件名里的 ABI 必须是 3.12 能装的，否则离线安装当场报错（cp314 就是本次的病例）。
TARGET_PYTHON = (3, 12)
TARGET_TAG = f"cp{TARGET_PYTHON[0]}{TARGET_PYTHON[1]}"
PURE_TAGS = {"py3-none-any", "py2.py3-none-any", "py2-none-any", "none-any"}
ALLOWED_PLATFORMS = {"any", "win_amd64"}


def tag_installable(tag: str) -> bool:
    """这个 wheel tag 在目标 Python（3.12 / win_amd64）上能不能装。

    `abi3` 是稳定 ABI，向前兼容：cp37-abi3 在 3.12 上能用，不能按主版本一刀切。
    """
    if tag in PURE_TAGS:
        return True
    parts = tag.split("-")
    if len(parts) != 3:
        return False
    interp, abi, plat = parts
    if plat not in ALLOWED_PLATFORMS:
        return False
    if abi == "any":
        return interp.startswith("py")
    if abi == "abi3":
        match = re.match(r"cp(\d{2,3})$", interp)
        return bool(match) and int(match.group(1)) <= int(TARGET_TAG[2:])
    return interp == TARGET_TAG and abi == TARGET_TAG


def _norm(name: str) -> str:
    """PEP 503 名字归一化：pip 自己就用这个规则比对包名。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_lock(path: Path = LOCK) -> list[tuple[str, str]]:
    """读 requirements.lock → [(归一化包名, 版本)]。只接受 `==` 钉版。"""
    pins: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            raise SystemExit(f"requirements.lock 里有没钉版的行：{line!r}")
        name, version = line.split("==", 1)
        pins.append((_norm(name), version.strip()))
    if not pins:
        raise SystemExit("requirements.lock 是空的")
    return pins


def scan_wheels(directory: Path) -> dict[tuple[str, str], list[str]]:
    """目录里的 wheel → {(包名, 版本): [tag, ...]}。文件名按 wheel 规范切。"""
    found: dict[tuple[str, str], list[str]] = {}
    for whl in sorted(directory.glob("*.whl")):
        parts = whl.stem.split("-")
        if len(parts) < 3:
            continue
        name, version = _norm(parts[0]), parts[1]
        tag = "-".join(parts[-3:])
        found.setdefault((name, version), []).append(tag)
    return found


def check_packages(directory: Path = PACKAGES, pins: list[tuple[str, str]] | None = None) -> list[str]:
    """返回问题清单（空 = 齐全）。缺包、版本不对、ABI 不对都会点名。"""
    pins = pins if pins is not None else parse_lock()
    wheels = scan_wheels(directory)
    problems: list[str] = []
    for name, version in pins:
        hits = wheels.get((name, version))
        if not hits:
            have = sorted(v for (n, v) in wheels if n == name)
            if have:
                problems.append(f"{name}: packages/ 里只有 {have}，锁文件要 {version}")
            else:
                problems.append(f"{name}=={version}: packages/ 里没有这个包")
            continue
        if not any(tag_installable(tag) for tag in hits):
            problems.append(
                f"{name}=={version}: wheel 的 ABI 是 {hits}，Python {TARGET_PYTHON[0]}.{TARGET_PYTHON[1]} 装不了"
            )
    return problems


def download_packages(directory: Path = PACKAGES) -> int:
    """按锁文件下载 wheel。只取 3.12 / win_amd64 与纯 py3，避免再混进别的 ABI。"""
    cmd = [
        sys.executable, "-m", "pip", "download", "-r", str(LOCK), "-d", str(directory),
        "--only-binary=:all:",
        "--python-version", f"{TARGET_PYTHON[0]}{TARGET_PYTHON[1]}",
        "--implementation", "cp",
        "--abi", f"cp{TARGET_PYTHON[0]}{TARGET_PYTHON[1]}",
        "--platform", "win_amd64",
    ]
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        # Windows cmd 默认 GBK：不重配的话下面这些中文全是乱码，而报告的可读性就是这个工具的全部价值。
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="#362 离线依赖包核对 / 重新下载")
    ap.add_argument("action", choices=("check", "download"))
    ap.add_argument("--dir", default=str(PACKAGES), help="wheel 目录，默认 packages/")
    args = ap.parse_args()
    directory = Path(args.dir)

    if args.action == "download":
        return download_packages(directory)

    if not directory.exists():
        print(f"FAIL  目录不存在：{directory}", flush=True)
        return 1
    problems = check_packages(directory)
    if problems:
        print(f"FAIL  packages/ 与 requirements.lock 对不上（{len(problems)} 条）:", flush=True)
        for line in problems:
            print(f"  - {line}", flush=True)
        print("修法：python tools/offline_packages.py download", flush=True)
        return 1
    print(f"PASS  packages/ 覆盖 requirements.lock 全部 {len(parse_lock())} 条", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
