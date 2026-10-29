"""#243: viewport 隔离断言的子进程 runner。

load_production_app 会弹掉并重建 sys.modules 里的 app.*/main——这是进程级
污染，绝不能跑在共享的 pytest 进程里（曾致 MCP 全量回归 12 个假失败）。
本脚本在独立进程执行断言，失败即以非零退出码反馈给宿主测试。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_conversation_browser_harness import run_viewport_isolation_assertions  # noqa: E402

with tempfile.TemporaryDirectory(prefix="firefly-issue13-subproc-") as temp_dir:
    run_viewport_isolation_assertions(Path(temp_dir))
print("viewport isolation assertions OK")
