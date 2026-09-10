"""Issue 96：Excel 导入浏览器端到端验收。

覆盖：
- 正常路径：拖拽 .xlsx → 预览面板（任务统计/Plan IR）→ 展开 → 8 个节点（DOM + 服务器落盘一致）
- 异常路径：非 xlsx 文件、损坏 xlsx、非 xlsx 文件名、空任务计划展开

运行：python tests\\excel_import_browser.py
"""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conversation_browser as harness  # noqa: E402
from test_excel_importer import make_xlsx_bytes  # noqa: E402

CLIENT_SCRIPT = Path(__file__).resolve().parent / "excel_import_browser_client.js"

# 1 个主图（1 个子任务）+ 1 个 A+ 方案（2 个子任务）→ 3 任务
FIXTURE_SHEETS = {
    "图片方案A": [
        ["名称", "要求"],
        ["1、主图1", "整体产品图"],
        ["A+1、卖点模块1", "卖点特写"],
        ["A+1、场景模块2", "使用场景"],
    ],
}


def seed_store(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("excel-import-e2e")
    return {
        "project_id": project["id"] if isinstance(project, dict) else project.id,
        "selected_text": "excel import e2e fixture",
    }


def build_xlsx_base64() -> str:
    xlsx_bytes = make_xlsx_bytes(FIXTURE_SHEETS)
    return base64.b64encode(xlsx_bytes).decode("ascii")


def main() -> int:
    browser = harness.find_browser()
    if not browser:
        print("未找到可用的 Chromium/Chrome 浏览器，跳过 Excel 导入浏览器验收。")
        return 0

    with tempfile.TemporaryDirectory(prefix="firefly-excel-e2e-") as root:
        store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
        fixture = seed_store(store)
        harness.CLIENT_SCRIPT = CLIENT_SCRIPT
        print(f"excel e2e project={fixture['project_id']} xlsx_bytes={len(make_xlsx_bytes(FIXTURE_SHEETS))}", flush=True)
        app = harness.load_production_app(Path(root) / "runtime", store)
        server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
        server.start()
        print(f"excel e2e server listening on {server.base_url}", flush=True)
        try:
            try:
                result = harness.launch(
                    browser,
                    server,
                    (1280, 800),
                    fixture,
                    result_timeout=180,
                    extra_query={"xlsx": build_xlsx_base64()},
                )
            except RuntimeError as error:
                print(f"Excel 导入浏览器验收未通过：{str(error).encode('gbk', errors='replace').decode('gbk')}")
                return 1
        finally:
            server.close()
            store.close()

    assertions = result.get("assertions") or []
    print(f"PASS: Excel 导入浏览器验收通过：{len(assertions)} 项断言全部通过。")
    for item in assertions:
        detail = f"（{item['detail']}）" if item.get("detail") else ""
        text = f"- {item['name']}{detail}"
        print(text.encode("gbk", errors="replace").decode("gbk"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
