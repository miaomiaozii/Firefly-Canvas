"""启动前的依赖体检（票 #363）。

票面现场：``run.bat`` 只 ``import fastapi,requests,uvicorn`` 三个模块，缺 Pillow 之类时
服务在隐藏窗口里崩，``tools/wait-for-local-server.ps1`` 等 60 秒后静默退出——用户什么也看不到。
这里把 ``requirements.txt`` 里每个依赖的**导入名**都试一遍，缺谁就用中文点名，并给出处理办法。

用法：
    python tools/check_dependencies.py            # 人读的输出（中文）
    python tools/check_dependencies.py --quiet    # 只打缺的部分，给启动器用
    python tools/check_dependencies.py --names-only          # 只回 ASCII 名单（启动器拼自己的中文提示）
    python tools/check_dependencies.py --extra-modules foo,bar   # 追加要试的导入名（测试用）

退出码：
    0  必需依赖齐（可选依赖缺了只提示，不拦启动）
    1  有必需依赖缺失 —— 启动器应当拦住并提示「双击 安装依赖.bat」
    2  脚本自身出问题（例如解释器异常）

可选依赖不拦启动的理由要写清楚：``httpx[socks]`` 只有走 SOCKS 代理时才用得到，
本机没装也照样跑；把它算成致命缺失会让正常机器的启动器一直红。
"""

from __future__ import annotations

import argparse
import importlib
import sys
from contextlib import redirect_stdout

# (requirements.txt 里的包名, 导入名（可多个，任一存在即算装过）, 是否必需)
DEPENDENCIES: list[tuple[str, tuple[str, ...], bool]] = [
    ("fastapi", ("fastapi",), True),
    ("uvicorn", ("uvicorn",), True),
    ("requests", ("requests",), True),
    ("pydantic", ("pydantic",), True),
    # 表单上传：新版 python-multipart 提供 python_multipart，老版本只有 multipart。
    ("python-multipart", ("python_multipart", "multipart"), True),
    ("httpx", ("httpx",), True),
    ("httpx[socks]", ("socks",), False),
    ("truststore", ("truststore",), True),
    ("pillow", ("PIL",), True),
    ("websockets", ("websockets",), True),
    # #256 OCR：缺了服务能起，但 OCR 功能会红；按必需算，装依赖时就该带上。
    ("rapidocr-onnxruntime", ("rapidocr_onnxruntime",), True),
]

REMEDY_LINES = [
    "处理办法：双击「安装依赖.bat」，装完再双击 run.bat。",
    "也可以在命令行里执行：python -m pip install -r requirements.txt",
]


def _module_present(name: str) -> bool:
    """实际导入才能发现缺 DLL 等问题；PIL.Image 会加载 Pillow 的原生模块。"""
    try:
        with redirect_stdout(sys.stderr):
            importlib.import_module("PIL.Image" if name == "PIL" else name)
        return True
    except Exception:
        return False


def check(extra_modules: tuple[str, ...] = ()) -> tuple[list[tuple[str, tuple[str, ...]]], list[tuple[str, tuple[str, ...]]], list[str]]:
    """返回 (缺的必需项, 缺的可选项, 追加项里缺的名字)。

    前两项是 ``(包名, 导入名元组)``，只给事实不管措辞——人读的输出和
    启动器拼中文提示要的 ASCII 名单各自格式化（见 ``main``）。
    """
    missing_required: list[tuple[str, tuple[str, ...]]] = []
    missing_optional: list[tuple[str, tuple[str, ...]]] = []
    for dist, modules, required in DEPENDENCIES:
        if any(_module_present(m) for m in modules):
            continue
        (missing_required if required else missing_optional).append((dist, modules))
    extra_missing = [name for name in extra_modules if not _module_present(name)]
    return missing_required, missing_optional, extra_missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动前检查 Python 依赖是否齐备")
    parser.add_argument("--quiet", action="store_true", help="只输出缺失项，给启动器调用用")
    parser.add_argument(
        "--names-only",
        action="store_true",
        help="只打缺失的包名和导入名（纯 ASCII）。启动器拿这些名字自己组中文提示——"
        "PowerShell 5.1 解码子进程输出用的是 OEM 码页，中间接中文文本容易乱码。",
    )
    parser.add_argument(
        "--extra-modules",
        default="",
        help="逗号分隔的额外导入名，按可选对待（测试用）",
    )
    args = parser.parse_args(argv)
    extra = tuple(x.strip() for x in args.extra_modules.split(",") if x.strip())

    try:
        missing_required, missing_optional, extra_missing = check(extra)
    except Exception as exc:  # pragma: no cover - 兜底：解释器本身出问题
        print(f"[依赖检查异常] {type(exc).__name__}: {exc}")
        return 2

    if args.names_only:
        # 给启动器用：只回纯 ASCII（包名 + 导入名），一行一个。可选项不报——
        # 它不拦启动，混进去只会让弹窗里的名单看不懂。
        for dist, modules in missing_required:
            print(f"{dist} ({' '.join(modules)})")
        return 1 if missing_required else 0

    for dist, modules in missing_optional:
        print(f"[可选依赖未安装] {dist}（导入名：{'、'.join(modules)}） —— 只有走 SOCKS 代理等场景才需要，不影响启动。")
    for name in extra_missing:
        print(f"[可选依赖未安装] {name} —— 追加检查项，不影响启动。")

    if missing_required:
        for dist, modules in missing_required:
            print(f"[缺少依赖] {dist}（导入名：{'、'.join(modules)}）")
        print(f"共缺少 {len(missing_required)} 个必需依赖，服务起不来。")
        for line in REMEDY_LINES:
            print(line)
        return 1

    if not args.quiet:
        total = sum(1 for _, _, required in DEPENDENCIES if required)
        print(f"依赖检查通过：{total} 个必需依赖都在。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
