# -*- coding: utf-8 -*-
"""#362 Python 版本守卫：确认当前解释器是 CPython 3.12，不是就用中文说清楚。

为什么中文提示放在 Python 里而不是 bat 里：实测 cmd 处理 UTF-8 bat 里的中文 echo 会错位
（同一段里两条几乎一样的中文行，一条正常、另一条被当成命令执行并报 "not recognized"）。
#363 已经因为同样的原因把依赖清单的中文挪进 tools/check_dependencies.py，这里沿用这个做法。

退出码：0 = 3.12；1 = 不是（已打印中文说明）。
"""
from __future__ import annotations

import sys

REQUIRED = (3, 12)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if sys.version_info[:2] == REQUIRED:
        return 0
    print("[错误] Python 版本不对：本项目统一用 Python 3.12。")
    print(f"       当前是 {sys.version.split()[0]}（{sys.executable}）")
    print("       为什么必须 3.12：")
    print("       - 便携包内置的是 embeddable 3.12.10（tools/build_portable.py 的 PY_VER）；")
    print("       - packages/ 里的 wheel 按 cp312 / win_amd64 下载，别的版本装不上；")
    print("       - rapidocr-onnxruntime 1.4.4 只支持到 3.12。")
    print("       怎么办：用仓库自带的 python\\ 目录，或安装 Python 3.12 后重跑 安装依赖.bat。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
