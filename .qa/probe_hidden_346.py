"""#346 勘查：逐页枚举带 hidden 属性的元素，看计算样式是否真的 display:none。

只读勘查，不改代码。跑法：PYTHONUTF8=1 python .qa/probe_hidden_346.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests.conversation_browser import load_production_app
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser

PROBE_JS = """(() => {
  const hidden = [...document.querySelectorAll('[hidden]')];
  const offenders = hidden.filter((el) => getComputedStyle(el).display !== 'none').map((el) => ({
    tag: el.tagName,
    id: el.id || null,
    cls: typeof el.className === 'string' ? el.className : null,
    display: getComputedStyle(el).display,
  }));
  return {total: hidden.length, offenders};
})()"""


def main() -> int:
    browser_path = find_browser("chrome")
    with tempfile.TemporaryDirectory(prefix="hidden346-") as root:
        store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
        project = store.create_project("Hidden Probe")
        store.flush(project["id"])
        app = load_production_app(Path(root) / "runtime", store)
        with AsgiBrowserServer(app) as server:
            browser = NativeBrowser.launch(browser_path)
            try:
                pages = {
                    "project-list (root)": f"{server.base_url}/",
                    "global-settings": f"{server.base_url}/static/global-settings.html",
                    "smart-canvas": (
                        f"{server.base_url}/static/smart-canvas.html"
                        f"?id={urllib.parse.quote(project['id'])}"
                        f"&project={urllib.parse.quote(project['id'])}&browserTest=1"
                    ),
                }
                summary = {}
                for label, url in pages.items():
                    browser.navigate(url, timeout=30)
                    browser.wait_for("document.readyState === 'complete'", timeout=20)
                    # 给前端一轮初始化时间（面板可能在 boot 里被创建/填充）
                    browser.wait_for("true", timeout=3)
                    result = browser.evaluate(PROBE_JS)
                    summary[label] = result
                    print(f"--- {label} --- hidden={result['total']} 未隐藏={len(result['offenders'])}")
                    for off in result["offenders"]:
                        print(f"  {off['display']:6} {off['tag']}#{off['id'] or '-'} .{off['cls'] or '-'}")
                    errors = browser.console_errors
                    if errors:
                        print(f"console_errors: {len(errors)} 第一条: {errors[0]}")
                out = Path(".qa/hidden346-baseline.json")
                out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"明细写入 {out}")
            finally:
                browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
