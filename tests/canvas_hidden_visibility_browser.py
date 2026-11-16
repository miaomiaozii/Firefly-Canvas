"""#346 页面级可见性守卫：带 `hidden` 属性的元素必须计算为 `display:none`。

断言读的是 `getComputedStyle(el).display`，不是「元素在不在 DOM 里」——#346 的根因正是
作者样式里的 `display:flex/grid` 压过浏览器 UA 的 `[hidden]{display:none}`，元素在 DOM 里
「存在」但根本没隐藏。

三件事：
1. 逐页扫描：每个 `[hidden]` 元素计算样式都是 `none`（并钉住 hidden 元素数量下限，防止空扫假绿）。
2. 层叠回归：#346 删掉的零散补丁（`.x[hidden]{display:none}`）在新全局规则下仍然隐藏得住。
3. 跨项目搜索面板（#337 那一例）：顶栏开 → Esc 关 → 再开 → × 关，每一态都按计算样式判定。
4. 设置页模型选择器（#372）：这页源码里 `hidden` 出现 0 次，全部运行时加上，
   交互前扫描必为 0 个元素——原来下限只钉了首页/画布页，这页是沉默的空扫。
   现在走完一个真实键盘交互：临时服务商（PUT /api/providers 注入已知模型列表，
   不依赖外部服务）→ 模型搜索框真键盘输入过滤 → 非匹配 `.model-option` 计算样式必须
   `display:none` → 清空恢复 → 再过滤，最后让页面停在过滤态跑扫描并钉下限。
   预交互扫描不钉下限（本来就该是 0），下限只作用于交互后扫描。

Direct runner — not collected by unittest discover. Run:
    PYTHONUTF8=1 python tests/canvas_hidden_visibility_browser.py [--artifacts-dir DIR]
门里的接线名：`canvas-hidden-visibility-evidence`（接 domain/trial/release；按 #167/#185/#269 先例
不进 fast——实测本 runner 6.8s，而 fast 预算 30s、存量已用 20s，超预算会被 mode-budget 守卫刷红）。
纯文本守卫 tests/test_canvas_hidden_attribute_semantics.py 不挂步骤，走 unit-contract 分片与全量 pytest。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests.conversation_browser import load_production_app
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser

VIEWPORT = (1440, 900)

# 修 #346 时删掉的零散补丁选择器。全局 `[hidden]{display:none !important}` 必须逐条接住它们，
# 否则删补丁就是把一个已隐藏的界面重新点亮。
DELETED_HIDDEN_PATCHES: dict[str, tuple[str, ...]] = {
    "/": (
        ".trash-panel",
        ".empty-state",
        ".cleanup-confirm-area",
        "#confirmCleanup",
        ".versions-confirm",
        "#versionsRestore",
    ),
    "/static/global-settings.html": (".model-option",),
    "/static/smart-canvas.html": (
        ".canvas-empty-state",
        ".conversation-node-panel",
        ".model-search-empty",
        ".direct-option",
        ".smart-trash-preview-backdrop",
        ".smart-marked-panel",
        ".smart-marked-export-files",
    ),
}

# 扫描到 0 个 hidden 元素也算通过，所以钉下限：这两页本来就有这些隐藏控件，扫少了说明页面没加载到位。
MIN_HIDDEN_ELEMENTS = {"/": 10, "/static/smart-canvas.html": 20}

# #372：设置页的 [hidden] 全是运行时加的（交互前扫不到），下限只钉在交互后扫描——
# 交互循环最后让页面停在「搜索过滤中」态，5 个模型选项里 4 个非匹配项必须处于隐藏态。
SETTINGS_MIN_HIDDEN_AFTER_CYCLE = 4

HIDDEN_SWEEP_JS = """(() => {
  const nodes = [...document.querySelectorAll('[hidden]')];
  const offenders = nodes
    .filter((el) => getComputedStyle(el).display !== 'none')
    .map((el) => ({
      tag: el.tagName,
      id: el.id || null,
      cls: typeof el.className === 'string' ? el.className : null,
      display: getComputedStyle(el).display,
    }));
  return {total: nodes.length, offenders};
})()"""

CASCADE_JS_TEMPLATE = """((selectors) => {
  return selectors.map((selector) => {
    let element = document.querySelector(selector);
    let synthetic = false;
    if (!element) {
      // 这个类由前端动态创建，静态 DOM 里没有；造一个同类的壳——断言的是层叠结果，不是 app 逻辑。
      element = document.createElement('div');
      element.className = selector.replace(/^[.#]/, '');
      document.body.appendChild(element);
      synthetic = true;
    }
    const before = element.hidden;
    element.hidden = true;
    const display = getComputedStyle(element).display;
    element.hidden = before;
    if (synthetic) {
      element.remove();
    }
    return {selector, display, synthetic, hidden_ok: display === 'none'};
  });
})(__SELECTORS__)"""

PANEL_STATE_JS = """(() => {
  const panel = document.querySelector('#canvasSearchPanel');
  if (!panel) {
    return {exists: false};
  }
  return {
    exists: true,
    hiddenAttribute: panel.hidden,
    display: getComputedStyle(panel).display,
    visible: getComputedStyle(panel).display !== 'none',
    ariaExpanded: document.querySelector('#smartSearchToggle')?.getAttribute('aria-expanded') || null,
  };
})()"""

# #372：设置页交互前注入一个带已知模型列表的临时服务商——
# 模型选择器（.model-picker）只有在 available_models 非空时才渲染搜索框和选项；
# base_url 指向永不连的端口，渲染路径不发起任何外部请求。
SETTINGS_SEED_PROVIDERS_JS = """(async () => {
  const provider = {
    id: 'e2e-hidden-guard',
    name: 'e2e hidden guard',
    base_url: 'http://127.0.0.1:9',
    protocol: 'openai',
    enabled: true,
    image_models: ['guard-model-a'],
    available_models: {
      image_models: ['guard-model-a', 'guard-model-b', 'guard-model-c', 'guard-model-d', 'guard-model-e'],
      chat_models: [],
      video_models: []
    }
  };
  const res = await fetch('/api/providers', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify([provider])
  });
  if (!res.ok) throw new Error('seed /api/providers failed: ' + res.status);
})()"""

SETTINGS_PICKER_STATE_JS = """(() => {
  // .model-picker-search 只在「有可用模型」的选择器里渲染——用它做锚点，
  // 与页面上其他无模型的（隐藏）选择器无关。
  const search = document.querySelector('.model-picker-search');
  if (!search) {
    return {exists: false};
  }
  const picker = search.closest('.model-picker');
  const options = [...picker.querySelectorAll('.model-option')];
  return {
    exists: true,
    pickerDisplay: getComputedStyle(picker).display,
    options: options.map((el) => ({
      text: el.textContent,
      hiddenAttribute: el.hidden,
      display: getComputedStyle(el).display
    }))
  };
})()"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # 默认落在 tmp/（.gitignore 内）：门跑不会弄脏 git，失败时现场还能人手复看。
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "tmp" / "qa" / "canvas-hidden-visibility",
    )
    return parser.parse_args(argv)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cascade_probe(browser: NativeBrowser, selectors: tuple[str, ...]) -> list[dict]:
    expression = CASCADE_JS_TEMPLATE.replace("__SELECTORS__", json.dumps(list(selectors)))
    return list(browser.evaluate(expression) or [])


def search_panel_cycle(browser: NativeBrowser, artifact_root: Path) -> dict:
    """#337 验收里那三条关闭路径，按计算样式判定（顶栏再点 / Esc / ×）。"""
    states: dict[str, dict] = {"closed_initial": browser.evaluate(PANEL_STATE_JS)}
    browser.native_click("#smartSearchToggle")
    browser.wait_for("!document.querySelector('#canvasSearchPanel').hidden", timeout=10)
    states["opened_by_toggle"] = browser.evaluate(PANEL_STATE_JS)
    # 留一张面板开着时的图：光看「关闭后不显示」看不出面板本来在哪，有对比才能看出真关上了。
    browser.capture_screenshot(artifact_root / "search-panel-open.png")
    browser.native_press("Escape")
    browser.wait_for("document.querySelector('#canvasSearchPanel').hidden === true", timeout=10)
    states["closed_by_escape"] = browser.evaluate(PANEL_STATE_JS)
    browser.native_click("#smartSearchToggle")
    browser.wait_for("!document.querySelector('#canvasSearchPanel').hidden", timeout=10)
    states["reopened_by_toggle"] = browser.evaluate(PANEL_STATE_JS)
    browser.native_click("#canvasSearchClose")
    browser.wait_for("document.querySelector('#canvasSearchPanel').hidden === true", timeout=10)
    states["closed_by_close_button"] = browser.evaluate(PANEL_STATE_JS)
    browser.capture_screenshot(artifact_root / "search-panel-after-cycle.png")
    return states


def assert_states(states: dict[str, dict], failures: list[str]) -> None:
    expect_visible = {
        "opened_by_toggle": True,
        "reopened_by_toggle": True,
        "closed_initial": False,
        "closed_by_escape": False,
        "closed_by_close_button": False,
    }
    for state, should_be_visible in expect_visible.items():
        snapshot = states.get(state) or {}
        if not snapshot.get("exists"):
            failures.append(f"search panel state {state}: #canvasSearchPanel 不存在")
            continue
        if bool(snapshot.get("visible")) is not should_be_visible:
            failures.append(
                f"search panel state {state}: visible={snapshot.get('visible')} "
                f"display={snapshot.get('display')} hiddenAttribute={snapshot.get('hiddenAttribute')} "
                f"期望 visible={should_be_visible}"
            )


def settings_model_picker_cycle(browser: NativeBrowser, artifact_root: Path) -> dict:
    """#372：设置页唯一会批量产生运行时 [hidden] 的交互——模型选择器搜索过滤。

    真键盘：聚焦 .model-picker-search → 输入 'guard-model-a'（5 个选项里只有同名项
    匹配）→ 4 个非匹配 .model-option 被加 [hidden]；Ctrl+A + Backspace 清空后恢复；
    再输入一次让页面停在过滤态，后续扫描的下限（SETTINGS_MIN_HIDDEN_AFTER_CYCLE）靠它。
    模仿 search_panel_cycle：每态按计算样式快照，最后留截图。"""
    states: dict[str, dict] = {}
    browser.evaluate(SETTINGS_SEED_PROVIDERS_JS, await_promise=True)
    browser.reload(timeout=30)
    browser.wait_for("!!document.querySelector('.model-picker-search')", timeout=20)
    states["loaded"] = browser.evaluate(SETTINGS_PICKER_STATE_JS)
    browser.native_click(".model-picker-search")
    browser.native_type("guard-model-a")
    browser.wait_for(
        "[...document.querySelectorAll('.model-option')].filter(o => o.hidden).length === 4",
        timeout=10,
    )
    states["filtered"] = browser.evaluate(SETTINGS_PICKER_STATE_JS)
    browser.capture_screenshot(artifact_root / "settings-model-filter.png")
    browser.native_press("a", modifiers=("Control",))
    browser.native_press("Backspace")
    browser.wait_for(
        "[...document.querySelectorAll('.model-option')].filter(o => o.hidden).length === 0",
        timeout=10,
    )
    states["cleared"] = browser.evaluate(SETTINGS_PICKER_STATE_JS)
    browser.native_type("guard-model-a")
    browser.wait_for(
        "[...document.querySelectorAll('.model-option')].filter(o => o.hidden).length === 4",
        timeout=10,
    )
    states["refiltered"] = browser.evaluate(SETTINGS_PICKER_STATE_JS)
    return states


def assert_settings_states(states: dict[str, dict], failures: list[str]) -> None:
    # query 非空 = 过滤态：只有文本含 query 的选项可见，其余必须隐藏且计算样式 display:none
    # ——后者就是 #346 全局 [hidden] 规则在设置页生效与否的直接判定。
    for name, query in (("loaded", ""), ("filtered", "guard-model-a"), ("cleared", ""), ("refiltered", "guard-model-a")):
        snapshot = states.get(name) or {}
        if not snapshot.get("exists"):
            failures.append(f"settings picker state {name}: .model-picker-search 不存在（服务商模型列表没渲染）")
            continue
        for option in snapshot.get("options") or []:
            should_hide = bool(query) and query not in (option.get("text") or "")
            if bool(option.get("hiddenAttribute")) is not should_hide:
                failures.append(
                    f"settings picker state {name}: {option.get('text')} hiddenAttribute="
                    f"{option.get('hiddenAttribute')}（期望隐藏={should_hide}）"
                )
            if option.get("display") == "none" and not should_hide:
                failures.append(
                    f"settings picker state {name}: {option.get('text')} 应可见但计算 display=none"
                )
            if should_hide and option.get("display") != "none":
                failures.append(
                    f"settings picker state {name}: {option.get('text')} 带 hidden 属性但计算 "
                    f"display={option.get('display')}（全局 [hidden] 规则在设置页没生效）"
                )


def run(artifacts_dir: Path) -> list[str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    sweeps: dict[str, dict] = {}
    cascades: dict[str, list[dict]] = {}
    panel_states: dict[str, dict] = {}
    settings_states: dict[str, dict] = {}
    chrome = find_browser("chrome")
    with tempfile.TemporaryDirectory(prefix="hidden-visibility-") as root:
        store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
        project = store.create_project("Hidden visibility guard")
        store.flush(project["id"])
        app = load_production_app(Path(root) / "runtime", store)
        with AsgiBrowserServer(app) as server:
            browser = NativeBrowser.launch(chrome)
            browser.cdp.timeout = 60
            try:
                browser.set_environment(VIEWPORT)
                canvas_url = (
                    f"{server.base_url}/static/smart-canvas.html"
                    f"?id={project['canvas_id']}&project={project['id']}&project_mode=1"
                )
                page_urls = {
                    "/": f"{server.base_url}/",
                    "/static/global-settings.html": f"{server.base_url}/static/global-settings.html",
                    "/static/smart-canvas.html": canvas_url,
                }
                for page, url in page_urls.items():
                    browser.navigate(url, timeout=30)
                    if page == "/static/smart-canvas.html":
                        browser.wait_for(
                            "typeof canvas !== 'undefined' && canvas?.format === 'firefly-canvas-v1' "
                            "&& Array.isArray(canvasState.nodes) "
                            "&& document.querySelector('#projectSaveState')?.dataset.state === 'saved'",
                            timeout=30,
                        )
                    else:
                        browser.wait_for("document.readyState === 'complete'", timeout=20)
                    sweep = browser.evaluate(HIDDEN_SWEEP_JS)
                    sweeps[page] = sweep
                    for offender in sweep["offenders"]:
                        failures.append(
                            f"{page}: [hidden] 仍然显示 display={offender['display']} "
                            f"{offender['tag']}#{offender['id'] or '-'} .{offender['cls'] or '-'}"
                        )
                    minimum = MIN_HIDDEN_ELEMENTS.get(page)
                    if minimum is not None and sweep["total"] < minimum:
                        failures.append(
                            f"{page}: 只扫到 {sweep['total']} 个 [hidden] 元素（下限 {minimum}）——"
                            "页面没加载到位，扫描不能算通过"
                        )
                    cascade = cascade_probe(browser, DELETED_HIDDEN_PATCHES[page])
                    cascades[page] = cascade
                    for item in cascade:
                        if not item["hidden_ok"]:
                            failures.append(
                                f"{page}: 删掉零散补丁后 {item['selector']} 不再隐藏"
                                f"（display={item['display']}）"
                            )
                    if page == "/static/smart-canvas.html":
                        panel_states = search_panel_cycle(browser, artifacts_dir)
                        assert_states(panel_states, failures)
                        final_sweep = browser.evaluate(HIDDEN_SWEEP_JS)
                        sweeps["/static/smart-canvas.html:after-cycle"] = final_sweep
                        for offender in final_sweep["offenders"]:
                            failures.append(
                                f"{page}（面板开合一轮后）: [hidden] 仍然显示 display={offender['display']} "
                                f"{offender['tag']}#{offender['id'] or '-'} .{offender['cls'] or '-'}"
                            )
                    if page == "/static/global-settings.html":
                        # #372：这页预交互扫描必为 0（hidden 全是运行时加的），
                        # 交互循环让页面停在「搜索过滤中」态，下限只钉在交互后扫描。
                        settings_states = settings_model_picker_cycle(browser, artifacts_dir)
                        assert_settings_states(settings_states, failures)
                        final_sweep = browser.evaluate(HIDDEN_SWEEP_JS)
                        sweeps["/static/global-settings.html:after-cycle"] = final_sweep
                        for offender in final_sweep["offenders"]:
                            failures.append(
                                f"{page}（交互后）: [hidden] 仍然显示 display={offender['display']} "
                                f"{offender['tag']}#{offender['id'] or '-'} .{offender['cls'] or '-'}"
                            )
                        if final_sweep["total"] < SETTINGS_MIN_HIDDEN_AFTER_CYCLE:
                            failures.append(
                                f"{page}（交互后）: 只扫到 {final_sweep['total']} 个 [hidden] 元素"
                                f"（下限 {SETTINGS_MIN_HIDDEN_AFTER_CYCLE}）——交互循环没产生预期隐藏态，不能算通过"
                            )
                browser.capture_screenshot(artifacts_dir / "final-frame.png")
            except Exception as error:  # 失败证据要落地，否则只能看到异常字符串
                write_json(
                    artifacts_dir / "failure.json",
                    {
                        "type": type(error).__name__,
                        "message": str(error),
                        "operations": len(browser.operations),
                        "console_errors": browser.console_errors,
                    },
                )
                raise
            finally:
                write_json(artifacts_dir / "hidden-sweeps.json", sweeps)
                write_json(artifacts_dir / "deleted-patch-cascades.json", cascades)
                write_json(artifacts_dir / "search-panel-states.json", panel_states)
                write_json(artifacts_dir / "settings-picker-states.json", settings_states)
                write_json(artifacts_dir / "console-errors.json", browser.console_errors)
                browser.close()
    write_json(artifacts_dir / "result.json", {"failures": failures})
    return failures


def main(argv: list[str] | None = None) -> int:
    # 固定 UTF-8：中文 Windows 默认 locale（GBK）会把中文失败信息弄成门无法解码的输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    print(f"artifacts_dir={args.artifacts_dir}", flush=True)
    failures = run(args.artifacts_dir)
    if failures:
        print(json.dumps({"verdict": "failed", "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"verdict": "passed", "failures": []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
