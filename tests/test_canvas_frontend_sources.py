"""画布前端拆分守卫（#154）。

`tests/canvas_frontend_sources.py::CANVAS_SCRIPTS` 必须与 `static/smart-canvas.html` 里
画布相关 `<script src>` 的顺序一致，否则按源码文本断言的测试会漏掉刚拆出去的模块。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.canvas_frontend_sources import CANVAS_SCRIPTS, ROOT, is_canvas_module
from tools.quality_gate import trusted_node_executable


def _node_for_syntax_check() -> str:
    """#167：语法检查用的 node 先取质量门的受信路径，而不是 PATH 上的 node。

    门会验证 PATH 里的 node 不是伪造的（test_javascript_syntax_uses_trusted_node_instead_of_path），
    所以这里不能先 `shutil.which("node")`；也不能因为找不到 node 就静默跳过。
    """
    trusted = trusted_node_executable()
    if Path(trusted).is_file():
        return trusted
    found = shutil.which("node")
    return found if found else trusted


def _html_canvas_scripts() -> list[str]:
    html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
    loaded = [src.split("?")[0] for src in re.findall(r'<script src="/static/js/([^"]+)"', html)]
    return [name for name in loaded if is_canvas_module(name)]


class CanvasFrontendSourceListTests(unittest.TestCase):
    def test_canvas_scripts_match_html_load_order(self):
        self.assertEqual(_html_canvas_scripts(), list(CANVAS_SCRIPTS))

    def test_every_listed_canvas_script_exists(self):
        for name in CANVAS_SCRIPTS:
            self.assertTrue((ROOT / "static" / "js" / name).is_file(), f"missing static/js/{name}")

    def test_smart_canvas_is_the_last_canvas_script_loaded(self):
        # smart-canvas.js 依赖前面模块的全局绑定，必须最后加载。
        self.assertEqual(CANVAS_SCRIPTS[-1], "smart-canvas.js")

    def test_navigation_index_module_table_covers_every_canvas_script(self):
        # #173 下沉时实际发生了文档漂移：navigation-index.md 的模块表里没有
        # canvas-core.js，正文还写「跨模块可变状态集中在 canvas-state.js」。
        # AGENTS.md 把这份索引当入口用，漏登记会让人按错的图景改代码。
        doc = (ROOT / "docs" / "agents" / "navigation-index.md").read_text(encoding="utf-8")
        listed = re.findall(r"^\|\s*`([A-Za-z0-9_-]+\.js)`", doc, re.M)
        self.assertEqual(sorted(set(CANVAS_SCRIPTS)), sorted(set(listed)))

    def test_every_canvas_module_passes_node_syntax_check(self):
        # #167：这条测试就是质量门的 javascript-syntax 步骤（fast/domain 都跑），
        # 所以不能因为 PATH 上没 node 就静默跳过，否则门会变成假绿。
        node = _node_for_syntax_check()
        self.assertTrue(
            Path(node).is_file(),
            f"node 不可用（{node}）：javascript-syntax 门必须真跑，不能静默跳过",
        )
        for name in CANVAS_SCRIPTS:
            try:
                # 必须有超时：node 挂在某个文件上时，整个测试进程会被Ctrl+C / 门杀掉，
                # 没超时的 node --check 就变成孤儿留在后台（2026-11-14 那 12 个 node --check 就是这么来的）。
                result = subprocess.run(
                    [node, "--check", str(ROOT / "static" / "js" / name)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                self.fail(f"static/js/{name}: node --check 30 秒还没返回，语法检查不能这样挂着")
            self.assertEqual(result.returncode, 0, f"static/js/{name}: {result.stderr.strip()}")

    def test_agent_node_path_literals_match_backend_prefix(self):
        # #170：#162 把裸全局 nodes 批量改成 canvasState.nodes 时，顺手污染了模板字符串里的
        # 路径段 agent-nodes/，前端全部 Agent 节点端点因此 404。路径段必须跟后端前缀一致。
        backend = (ROOT / "app" / "routers" / "api_agent_nodes.py").read_text(encoding="utf-8")
        match = re.search(r'prefix="(/api/projects/\{project_id\}/[^"]+)"', backend)
        self.assertIsNotNone(match, "未能从 api_agent_nodes.py 解析出路由前缀")
        expected = match.group(1).split("/")[-1]
        source = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")
        segments = {
            m.group(1)
            for m in re.finditer(r'/api/projects/\$\{[^}]*\}/([^/"\'`$\{]+)', source)
        }
        self.assertTrue(segments, "canvas-agent-tui.js 里找不到 Agent 节点端点字面量")
        self.assertEqual(segments, {expected}, f"前端路径段 {segments} 与后端前缀 {expected} 不一致")

    def test_no_state_container_rename_inside_path_literals(self):
        # 同一批盲替换的通用守卫：状态容器名不得出现在 URL 字面量或路径注释里。
        offenders: list[str] = []
        for name in CANVAS_SCRIPTS:
            source = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for path in re.findall(r"/api/[A-Za-z0-9/_${}.-]*", line):
                    if "canvasState." in path:
                        offenders.append(f"static/js/{name}:{lineno} {path}")
        self.assertEqual(offenders, [], "路径字面量被状态容器改名污染：\n" + "\n".join(offenders))

    def test_marked_download_records_control_is_wired(self):
        # #228：打包回执不能只闪一次 toast，已标记面板要有本机回查入口。
        # 整页不在单测的 vm 里，所以这里钉住三处接线：HTML 控件、核心点击处理、
        # 下载模块提供的渲染函数。任一处改名都会静默失效（点了没反应）。
        html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        hub = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        module = (ROOT / "static" / "js" / "canvas-download.js").read_text(encoding="utf-8")
        self.assertIn('id="smartMarkedRecords"', html)
        self.assertIn('id="smartMarkedRecordsList"', html)
        self.assertIn("getElementById('smartMarkedRecords')", hub)
        self.assertIn("canvasDownloadRecordsHtml()", hub)
        self.assertIn("function canvasDownloadRecordsHtml", module)
        # 空态/按钮文案走 i18n，key 没定义就会把 key 直接显示给用户。
        i18n = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        for key in ("smart.downloadRecords", "smart.downloadRecordsEmpty"):
            self.assertIn(f'"{key}"', i18n)

    def test_canvas_assets_share_one_cache_buster(self):
        # 事故复盘（#224）：smart-canvas.html 里曾并存四个不同的 ?v= 值，
        # 改了 canvas-media-history.js 却把它的戳记停在两天前——浏览器继续用旧文件，
        # 用户 Ctrl+F5 也拿不到修复，而我一度把这种“没变化”当成代码缺陷在查。
        # 我自己也犯过一次：只刷新了 script 标签，漏了 css，直接弄红
        # test_creative_history_experience（它只比对 smart-canvas.css 与 smart-canvas.js 两个文件）。
        # 仓库有 Bump-AssetVersions（tools/restart-firefly.ps1）但没人跡。
        # 画布资源是一起发布的：整页所有 ?v= 必须同一个值。
        html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        tagged = re.findall(r'(/static/[^"\s]+)\?v=([0-9][0-9.]*)', html)
        self.assertTrue(tagged, "smart-canvas.html 里找不到任何带 ?v= 的本地资源")
        distinct = {value for _, value in tagged}
        self.assertEqual(
            len(distinct),
            1,
            "同一页的本地资源缓存戳必须一致（不一致 = 有人改文件没跡版本，用户拿到旧代码）：\n"
            + "\n".join(f"  {path} -> {value}" for path, value in tagged if value != max(distinct)),
        )
        stamped_scripts = {path.rsplit("/", 1)[-1] for path, _ in tagged if path.endswith(".js")}
        missing = [name for name in ("smart-canvas.js", *CANVAS_SCRIPTS) if name not in stamped_scripts]
        self.assertEqual(missing, [], f"以下画布脚本没带缓存戳进 HTML：{missing}")


    # #371：上面那条守卫只钉 .js（筛 `stamped_scripts` 时把 .css 丢了）——canvas-search.css 在
    # smart-canvas.html:30 上连 ?v= 都没有，改完 CSS 用户永远拿旧的。现在把三个产品页的
    # 本地资产（css + js）一律纳进来。
    PRODUCT_PAGES = ("smart-canvas.html", "project-list.html", "global-settings.html")
    # 新增样式表时必须按页里的 link 顺序登记（与 CANVAS_SCRIPTS 同一约定）。
    CANVAS_STYLESHEETS = (
        "studio-foundation.css",
        "smart-canvas.css",
        "excel-import.css",
        "canvas-annotations.css",
        # #324 的跨项目搜索面板样式：当时只登记了 canvas-search.js，CSS 漏了（#371）。
        "canvas-search.css",
    )

    def page_html(self, name: str) -> str:
        return (ROOT / "static" / name).read_text(encoding="utf-8")

    def test_local_assets_on_product_pages_all_carry_a_cache_stamp(self):
        unstamped = []
        for page in self.PRODUCT_PAGES:
            for match in re.finditer(r'(?:href|src)="(/static/[^"]+)"', self.page_html(page)):
                path = match.group(1)
                # 只要求代码资产（js/css）带戳：链另一个 .html 和图片不是缓存事故源头。
                if not re.search(r"\.(js|css)$", path.split("?")[0]):
                    continue
                if "?v=" not in path:
                    unstamped.append(f"{page} -> {path}")
        self.assertEqual(
            [],
            unstamped,
            "产品页里的 /static/ 资产必须带 ?v= 缓存戳（不带 = 改了代码用户拿不到，重启也不刷）",
        )

    def test_canvas_stylesheets_are_registered_and_stamped(self):
        html = self.page_html("smart-canvas.html")
        tagged = {path.split("?")[0].rsplit("/", 1)[-1]: value
                  for path, value in re.findall(r'href="(/static/css/[^"]+)\?v=([0-9][0-9.]*)"', html)}
        missing_files = [name for name in self.CANVAS_STYLESHEETS if not (ROOT / "static" / "css" / name).is_file()]
        self.assertEqual([], missing_files, f"登记了的样式表不在盘上：{missing_files}")
        missing = [name for name in self.CANVAS_STYLESHEETS if name not in tagged]
        self.assertEqual(missing, [], f"以下画布样式表没带缓存戳进 HTML：{missing}")
        linked = {path.split("?")[0].rsplit("/", 1)[-1]
                  for path in re.findall(r'href="(/static/css/[^"]+)"', html)}
        unregistered = sorted(linked - set(self.CANVAS_STYLESHEETS))
        self.assertEqual(unregistered, [], f"HTML 里链了但未登记的样式表：{unregistered}")

    def test_css_imports_carry_a_cache_stamp(self):
        # 样式表里的 @import 不在 restart/refresh 的 bump 正则范围内（那个只碰 HTML 的 href/src），
        # 所以只能靠这里钉：没 ?v= 的 @import 永远拿旧文件。
        offenders = []
        for css in (ROOT / "static" / "css").glob("*.css"):
            text = css.read_text(encoding="utf-8")
            for match in re.finditer(r"@import\s+url\(['\"]?(/static/[^'\")]+)", text):
                if "?v=" not in match.group(1):
                    offenders.append(f"{css.name} -> {match.group(1)}")
        self.assertEqual([], offenders, "样式表里的 @import 必须带 ?v=")

    def test_product_pages_share_one_stamp_across_the_repo(self):
        # 三页共用 studio-foundation.css / theme.js 这些资产。tools/refresh-cache.ps1 刷所有页，
        # 而 tools/restart-firefly.ps1 以前只刷 smart-canvas.html——那意味着改完 CSS 走普通重启，
        # project-list.html / global-settings.html 的戳停在旧值。两边现在刷同一批页。
        stamps = {}
        for page in self.PRODUCT_PAGES:
            values = set(re.findall(r'"/static/[^"]+\?v=([0-9][0-9.]*)"', self.page_html(page)))
            self.assertEqual(len(values), 1, f"{page} 自己就没共用一个戳：{sorted(values)}")
            stamps[page] = values.pop()
        distinct = set(stamps.values())
        self.assertEqual(
            len(distinct), 1,
            "三个产品页的缓存戳必须同值（不同值 = 某个启动器没刷到那页）：\n"
            + "\n".join(f"  {page} -> {value}" for page, value in stamps.items()),
        )

    def test_restart_and_refresh_bump_the_same_product_pages(self):
        restart = (ROOT / "tools" / "restart-firefly.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "static\\*.html",
            restart,
            "restart-firefly.ps1 必须刷所有 static\\*.html（只刷 smart-canvas.html 会让另两页的 CSS/JS 停在旧戳）",
        )


class SwallowedErrorRatchetTests(unittest.TestCase):
    """#348：吞错 ratchet 守卫。

    空 catch / 只 console 的 catch / 空 .catch(()=>{}) 属于「错误被吞」：
    用户触发的操作失败必须提示（ffToast），后台/可忽略的必须 console.warn 并加
    `// #348:` 标记注释说明为什么可以忽略。本测试扫全部 static/**.js + 产品页内联脚本，
    任何「没有实质处理、又没有 #348 标记」的吞错点都会红——新增吞错必须先接线。
    """

    MARKER = "#348"

    def _js_sources(self) -> list[tuple[str, str]]:
        sources = []
        for js in sorted((ROOT / "static").rglob("*.js")):
            sources.append((js.relative_to(ROOT).as_posix(), js.read_text(encoding="utf-8")))
        # 产品页内联 <script>（如 smart-canvas.html 的早期主题应用）同样在范围内
        for page in ("smart-canvas.html", "project-list.html", "global-settings.html"):
            html = (ROOT / "static" / page).read_text(encoding="utf-8")
            for m in re.finditer(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S):
                if "catch" in m.group(1):
                    sources.append((f"{page} (inline)", m.group(1)))
        return sources

    @staticmethod
    def _strip_js_comments(src: str) -> str:
        """去掉 // 与 /* */ 注释，用空格等长替换（保留换行）——行号/偏移与原始源码对齐。"""
        out = list(src)
        i, n = 0, len(src)
        while i < n:
            c = src[i]
            if c in "\"'`":
                q = c
                j = i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == q:
                        break
                    j += 1
                i = j + 1
            elif c == "/" and i + 1 < n and src[i + 1] == "/":
                while i < n and src[i] != "\n":
                    out[i] = " "
                    i += 1
            elif c == "/" and i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                end = n if j == -1 else j + 2
                for k in range(i, end):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
            else:
                i += 1
        return "".join(out)

    @staticmethod
    def _match_brace(src: str, open_idx: int) -> int:
        """src[open_idx] 必须是 '{'，返回配对 '}' 的下标（跳过字符串），失败返回 -1。"""
        depth = 0
        i, n = open_idx, len(src)
        while i < n:
            c = src[i]
            if c in "\"'`":
                q = c
                i += 1
                while i < n:
                    if src[i] == "\\":
                        i += 2
                        continue
                    if src[i] == q:
                        break
                    i += 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    @classmethod
    def _swallowed_sites(cls, src: str) -> list[tuple[int, str]]:
        """返回 (行号, 原始块文本)：空 catch / 只 console 的 catch / 空 .catch(箭头)。"""
        stripped = cls._strip_js_comments(src)
        sites: list[tuple[int, int]] = []  # (catch 起点, 块 '{' 下标)
        for m in re.finditer(r"\bcatch\s*(?:\(\s*[\w$]*\s*\))?\s*\{", stripped):
            sites.append((m.start(), m.end() - 1))
        for m in re.finditer(r"\.catch\s*\(", stripped):
            # 只认箭头函数体：`.catch(` 后第一个 `=> {` 的 `{`
            am = re.search(r"=>\s*\{", stripped[m.end():m.end() + 300])
            if am:
                sites.append((m.start(), m.end() + am.end() - 1))
        out: list[tuple[int, str]] = []
        for origin, open_idx in sites:
            close_idx = cls._match_brace(stripped, open_idx)
            if close_idx == -1:
                continue
            body = stripped[open_idx + 1 : close_idx]
            body_no_console = re.sub(r"\bconsole\s*\.\s*\w+\s*\(.*?\)\s*;?", "", body, flags=re.S)
            if body_no_console.strip():
                continue  # 有实质处理，不算吞错
            line = stripped[:origin].count("\n") + 1
            out.append((line, src[open_idx : close_idx + 1]))
        return out

    def test_no_unmarked_swallowed_error_sites(self):
        offenders = []
        for name, src in self._js_sources():
            for line, raw in self._swallowed_sites(src):
                if self.MARKER not in raw:
                    offenders.append(f"{name}:{line}")
        self.assertEqual(
            offenders,
            [],
            "发现未接线的吞错点（空 catch / 只 console）。用户触发 → window.ffToast.error（ui-toast.js）；"
            "后台/可忽略 → console.warn 并在 catch 块内加 `// #348:` 注释说明原因。违规：" + ", ".join(offenders),
        )

    def test_product_pages_load_ui_toast_before_business_scripts(self):
        # #348：ffToast 必须在业务脚本之前可用（theme.js 之后）。
        for page in ("smart-canvas.html", "project-list.html", "global-settings.html"):
            html = (ROOT / "static" / page).read_text(encoding="utf-8")
            self.assertIn("/static/js/ui-toast.js", html, f"{page} 没加载 ui-toast.js")
            toast_pos = html.index("/static/js/ui-toast.js")
            for script in ("global-preferences.js", "project-list.js", "global-settings.js", "smart-canvas.js"):
                m = re.search(rf"/static/js/{re.escape(script)}\?v=", html)
                if not m:
                    continue  # 该页不加载这个脚本
                self.assertLess(toast_pos, m.start(), f"{page}: ui-toast.js 必须在 {script} 之前加载")


class TestNoNativeDialogs(unittest.TestCase):
    """#347：产品页禁用原生 confirm()/prompt()——原生弹窗阻塞整页且样式不统一，
    改用应用内对话框 window.ffDialog（ui-dialog.js）：确认 → ask，输入 → input。
    本测试扫全部 static/**.js + 产品页内联脚本，出现原生对话框调用即红。
    """

    _DIALOG_CALL = re.compile(r"(?<![\w$])(confirm|prompt)\s*\(")

    def _js_sources(self) -> list[tuple[str, str]]:
        sources = []
        for js in sorted((ROOT / "static").rglob("*.js")):
            sources.append((js.relative_to(ROOT).as_posix(), js.read_text(encoding="utf-8")))
        for page in ("smart-canvas.html", "project-list.html", "global-settings.html"):
            html = (ROOT / "static" / page).read_text(encoding="utf-8")
            for m in re.finditer(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S):
                sources.append((f"{page} (inline)", m.group(1)))
        return sources

    def test_no_native_dialog_calls(self):
        offenders = []
        for name, src in self._js_sources():
            stripped = self._strip_strings(SwallowedErrorRatchetTests._strip_js_comments(src))
            for m in self._DIALOG_CALL.finditer(stripped):
                line = stripped[: m.start()].count("\n") + 1
                offenders.append(f"{name}:{line}({m.group(1)})")
        self.assertEqual(
            offenders,
            [],
            "禁止原生 confirm/prompt——用 window.ffDialog.ask / window.ffDialog.input（ui-dialog.js）。违规：" + ", ".join(offenders),
        )

    @staticmethod
    def _strip_strings(src: str) -> str:
        """在注释已去的基础上，再把字符串字面量内容换成空格（保留长度）——
        i18n 文案里的 "prompt(s)" 等英文不是调用。"""
        out = list(src)
        i, n = 0, len(src)
        while i < n:
            c = src[i]
            if c in "\"'`":
                q = c
                j = i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == q:
                        break
                    j += 1
                for k in range(i, min(j + 1, n)):
                    if out[k] != "\n":
                        out[k] = " "
                i = j + 1
            else:
                i += 1
        return "".join(out)


class TestUiToastNetworkErrorMapping(unittest.TestCase):
    """#347（并入 #365 跟进项）：ffToast.error 把常见英文浏览器网络错误映射成中文，
    不认识的保留原文。node 里抽出 ui-toast.js 的 NET_ERROR_ZH + zhNetworkError
    真实代码执行（vm 上下文，非副本），断言映射行为。"""

    def test_mapping_applies_to_error_only(self):
        src = (ROOT / "static" / "js" / "ui-toast.js").read_text(encoding="utf-8")
        self.assertIn('if (type === "error") msg = zhNetworkError(msg);', src)

    def test_zh_network_error_cases(self):
        node = _node_for_syntax_check()
        self.assertTrue(Path(node).is_file(), f"node 不可用（{node}）：映射测试必须真跑")
        # 质量门只允许 `node tests/*.mjs`（单参数）——harness 是已提交文件，
        # 内部从 static/js/ui-toast.js 抽真实 NET_ERROR_ZH/zhNetworkError 执行。
        proc = subprocess.run(
            [node, "tests/ui-toast-network-error-harness.mjs"],
            capture_output=True, text=True, timeout=60, cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, f"映射用例失败：{proc.stderr.strip()}")


class TestI18nCoreZhOnly(unittest.TestCase):
    """#365：i18n-core 只保留中文——旧 'en'（localStorage / set('en') / document lang）一律落到 zh，
    toggle 入口删除。node 里用 vm 沙箱执行 static/js/i18n-core.js 的真实代码
    （harness 是已提交文件，与 ui-toast-network-error-harness.mjs 同一约定）。"""

    def test_legacy_en_migrates_to_zh(self):
        node = _node_for_syntax_check()
        self.assertTrue(Path(node).is_file(), f"node 不可用（{node}）：i18n 中文单源测试必须真跑")
        proc = subprocess.run(
            [node, "tests/i18n-core-zh-only-harness.mjs"],
            capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=ROOT,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"i18n-core 中文单源行为断言失败：{proc.stdout.strip()}\n{proc.stderr.strip()}",
        )


if __name__ == "__main__":
    unittest.main()
