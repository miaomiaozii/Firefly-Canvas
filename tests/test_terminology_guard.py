"""Terminology consistency guard test (issue #139).

Prevents _Avoid_ terms from CONTEXT.md from reappearing in code/i18n/tests.
This is the third layer of the #115 root cure: a guard test that fails CI
when terminology drift is introduced.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_context_terms():
    """Parse CONTEXT.md and extract formal terms and _Avoid_ terms.

    Returns:
        tuple: (formal_terms: set, avoid_terms: set)
    """
    context_path = ROOT / "CONTEXT.md"
    content = context_path.read_text(encoding="utf-8")

    formal_terms = set()
    avoid_terms = set()

    for line in content.splitlines():
        # Match formal terms: **term**：
        match = re.match(r'\*\*(.+?)\*\*：', line)
        if match:
            formal_terms.add(match.group(1))
            continue

        # Match _Avoid_ lines: _Avoid_: 词1、词2、词3
        if line.startswith("_Avoid_"):
            terms_part = line.split(": ", 1)[1].strip()
            terms = [t.strip() for t in terms_part.split("、")]
            avoid_terms.update(terms)

    return formal_terms, avoid_terms


# Curated list of forbidden terms derived from CONTEXT.md _Avoid_ lines.
# These are terms that should not appear in code/i18n/tests.
# Excludes terms that are also formal terms for other concepts.
FORBIDDEN_TERMS = {
    # 核心画布 _Avoid_
    "智能画布", "主画布", "普通画布",
    # 经典画布 _Avoid_
    "旧画布",
    # 对话节点 _Avoid_
    "聊天框", "提示词对话", "LLM 面板",
    # Agent 节点 _Avoid_
    "对话模式", "隐藏工具调用", "全局 Agent",
    # 提示词节点 _Avoid_ (对话节点 is a formal term, excluded)
    "聊天记录", "消息节点",
    # 提示词提取 _Avoid_
    "发送整段对话", "自动连接", "上下文继承",
    # 项目 _Avoid_
    "画布分类", "画布集合", "项目标签", "全局工作区",
    # 项目目录 _Avoid_
    "全局数据目录", "单个画布文件",
    # 项目库 _Avoid_
    "Git 仓库", "默认项目",
    # Fly 项目包 _Avoid_
    "运行中项目文件", "画布 JSON", "ZIP 项目",
    # 项目回收站 _Avoid_ (默认项目 already listed)
    "画布回收站",
    # 项目内容 _Avoid_
    "全局数据", "运行缓存",
    # 项目媒体原件 _Avoid_
    "外链", "临时 URL", "画布内嵌媒体",
    # 生成原件 _Avoid_ (生成输出项 is a formal term, excluded)
    "缓存图", "缩略图", "节点副本",
    # 生成输出项 _Avoid_ (生成原件 is a formal term, excluded)
    "数组下标", "输出槽位", "缩略图 DOM",
    # 生成任务 _Avoid_ (生成记录, 生成原件 are formal terms, excluded)
    "结果节点",
    # 任务状态卡 _Avoid_ (生成记录, 生成历史 are formal terms, excluded)
    "临时日志",
    # 生成记录 _Avoid_ (任务状态卡, 生成原件 are formal terms, excluded)
    "调试日志",
    # 删除当前原图 _Avoid_
    "删除生成记录", "项目范围删除原件", "清理缓存", "删除节点",
    # 项目范围删除生成原件 _Avoid_ (删除当前原图, 项目清理 are formal terms, excluded)
    # 项目清理 _Avoid_
    "删除原图", "清空项目回收站", "自动清理",
    # 心流模式 _Avoid_
    "放大文本框", "全屏画布", "自动生成", "隐藏结构后丢失结构",
    # 生成历史 _Avoid_
    "生成日志", "全局历史",
    # 历史抽屉 _Avoid_
    "日志弹窗", "全屏历史页", "全局侧栏",
    # PS 编辑结果 _Avoid_
    "覆盖原图", "全局 PS 文件", "临时返回图",
    # PS 连接器 _Avoid_
    "PS 生成面板", "资产库插件", "完整画布插件",
    # 全局设置 _Avoid_ (项目内容 is a formal term, excluded)
    "项目设置",
    # 全局资源库 _Avoid_
    "项目素材库", "隐式共享目录",
    # 素材抽屉 _Avoid_（#212：用户可见文案、存量数据与内部注释改名完成后，才把裸词“资产库”纳入守卫；
    # CONTEXT.md 的“资源库/素材抽屉/项目回收区”三处词条均将其登记为 _Avoid_）
    "独立素材页面", "混合素材库", "全局资产面板", "资产库",
    # 在线生成 _Avoid_
    "本地生成", "ComfyUI", "工作流生成",
    # 模型路由 _Avoid_
    "参数降级", "静默改尺寸", "固定模型别名",
    # 角度参考 _Avoid_
    "角度控制页面", "专用角度引擎",
    # 局部修改 _Avoid_
    "局部重绘", "蒙版生图", "整图修改",
    # 局部保留 _Avoid_ (局部修改 is a formal term, excluded)
    "上传蒙版", "仅生成选区",
    # 局部编辑节点 _Avoid_
    "原图编辑模式", "图片节点工具栏", "原位覆盖",
    # 扩图 _Avoid_
    "局部编辑", "裁剪", "缩放", "图像放大",
    # CLI 适配器 _Avoid_
    "本地模型", "本地生成引擎",
    # 视频创作 _Avoid_
    "独立视频应用", "本地视频工作流",
    # 音频素材 _Avoid_
    "独立音频项目", "音频工作站",
    # Intentionally NOT curated from new _Avoid_ lines (#171): 待恢复任务 的
    # "重试"/"重新生成" and 循环生成 的 "批量生成" are legitimate words for
    # other concepts elsewhere in the codebase, so enforcing them would be wrong.
    # Only "输出槽位" (above) is enforced among the #171 additions.
}

# English patterns that indicate terminology drift (e.g., log used for history).
# Pattern name -> regex
FORBIDDEN_PATTERNS = {
    # History-related: log should be history (see #137)
    "log_id": r"\blog_id\b",
    # "logs" as a string literal key (legacy key or directory name)
    # Matches: ["logs"], 'logs', "logs" (string literals)
    # Does not match: source_logs, canvas.logs, logs.push(...)
    "logs (as history field)": r"['\"\[]logs\b",
    "generationLogs": r"\bgenerationLogs\b",
    "gen_log": r"\bgen_log\b",
    "historyLog": r"\bhistoryLog\b",
    "smartLogToggle": r"\bsmartLogToggle\b",
}

# Whitelist: (file_prefix, term_or_pattern, reason)
# A hit is exempt if the file path starts with file_prefix (or "*" for any)
# AND the term/pattern matches.
WHITELIST = [
    # smart-* implementation identifiers (#136 decided 2026-09-24: zero-migration,
    # documented mapping in CONTEXT.md "节点类型标识符" + app/constants.py NODE_TYPE_*)
    ("*", "smart-prompt", "#136 implementation identifier, zero-migration decision"),
    ("*", "smart-image", "#136 implementation identifier, zero-migration decision"),
    ("*", "smart-agent", "#136 implementation identifier, zero-migration decision"),
    ("*", "smart-conversation", "#136 implementation identifier, zero-migration decision"),
    # Real logging (not history)
    ("*", "logger", "Real logging, not history"),
    ("*", "logging", "Real logging module"),
    ("*", "console.log", "Browser console logging"),
    ("*", "diagnostics", "Diagnostics logging"),
    # Backward-compatible log_id read in deletion snapshots
    ("firefly_projects_deletion.py", "log_id", "Backward-compatible read of legacy log_id key"),
    # #212: 默认库历史名一次性迁移——迁移表/迁移测试必须保留历史名本身用于识别存量数据，
    # 不是显示文案（与 legacy log_id 兼容读取同类）
    ("app/services/misc.py", "资产库", "#212 legacy-name migration table DEFAULT_LIBRARY_LEGACY_NAMES"),
    ("tests/test_asset_library_naming.py", "资产库", "#212 migration tests reference legacy display names"),
    # "logs" in legitimate contexts (directory paths, legacy keys)
    ("CLI/windows/gemini/install_gemini_cli.ps1", "logs (as history field)", "PowerShell $logDir variable"),
    ("CLI/windows/openai/install_openai_codex_cli.ps1", "logs (as history field)", "PowerShell $logDir variable"),
    ("tools/jimeng_cli_install.ps1", "logs (as history field)", "PowerShell $logDir variable"),
    ("tools/jimeng_cli_login.ps1", "logs (as history field)", "PowerShell $logDir variable"),
    ("firefly_diagnostics.py", "logs (as history field)", "Diagnostics log directory path"),
    ("firefly_projects.py", "logs (as history field)", "Legacy logs key compatibility"),
    # fal.ai 队列协议的上游响应字段：status 返回体的 logs 是服务商契约字段名，不是画布历史字段
    ("app/services/generation.py", "logs (as history field)", "fal queue status response field (upstream contract)"),
    ("tests/test_fal_queue_image_mode.py", "logs (as history field)", "fal queue ERRORED status fixture mirroring upstream field"),
    # File-level whitelists for terms with few hits
    ("static/js/smart-canvas.js", "ComfyUI", "Comment about removed ComfyUI integration"),
    ("app/services/projects.py", "默认项目", "Docstring: core product no longer creates default projects"),
    ("tests/test_project_store.py", "外链", "Project name '历史外链' (History External Link)"),
    ("tests/history_drawer_browser.py", "删除原图", "Test prompt: imported assets cannot delete original"),
    ("static/js/i18n/canvas.js", "删除原图", "Trash confirm text: deleted originals cannot be restored"),
    ("static/js/i18n/smart-canvas.js", "删除原图", "Button: delete original + all references (project-wide delete)"),
    # Global whitelists for common terms with many hits
    ("*", "删除生成记录", "Formal operation name on the formal term '生成记录'"),
    ("*", "删除节点", "Common phrase: delete node"),
    ("*", "局部编辑", "Common phrase: local edit"),
    ("*", "画布 JSON", "Common phrase: canvas JSON file"),
    ("*", "结果节点", "Common phrase: result node"),
    ("*", "缩放", "Common word: zoom"),
    ("*", "缩略图", "Common word: thumbnail"),
    ("*", "缩略图 DOM", "Common phrase: thumbnail DOM"),
    ("*", "自动清理", "Common phrase: auto cleanup (trash auto-empty)"),
    ("*", "自动生成", "Common adjective: auto-generated"),
    ("*", "裁剪", "Common word: crop"),
]


def is_whitelisted(rel_path_str, term):
    """Check if a term in a file is whitelisted."""
    # Normalize path separators for cross-platform matching
    normalized_path = rel_path_str.replace("\\", "/")
    for file_prefix, whitelist_term, _reason in WHITELIST:
        if file_prefix == "*" or normalized_path.startswith(file_prefix):
            if term == whitelist_term:
                return True
    return False


def scan_file(file_path, rel_path):
    """Scan a single file for forbidden terms and patterns.

    Returns:
        list: [(rel_path, line_number, term, snippet), ...]
    """
    violations = []

    # Exclude the guard test itself
    if rel_path == Path("tests/test_terminology_guard.py"):
        return violations

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # Binary file or read error, skip
        return violations

    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        # Check Chinese terms
        for term in FORBIDDEN_TERMS:
            if term in line:
                if not is_whitelisted(str(rel_path), term):
                    violations.append((rel_path, i, term, line.strip()[:80]))

        # Check English patterns
        for pattern_name, pattern in FORBIDDEN_PATTERNS.items():
            if re.search(pattern, line):
                if not is_whitelisted(str(rel_path), pattern_name):
                    violations.append((rel_path, i, pattern_name, line.strip()[:80]))

    return violations


def scan_codebase():
    """Scan the codebase for forbidden terms and patterns.

    Returns:
        list: [(rel_path, line_number, term, snippet), ...]
    """
    violations = []

    # Directories to scan
    scan_dirs = ["app", "tests", "static", "CLI", "API", "tools", "plan_ir"]
    # Root .py files to scan
    root_py_files = [
        "main.py", "firefly_diagnostics.py", "firefly_generation.py",
        "firefly_projects.py", "firefly_projects_cleanup.py",
        "firefly_projects_deletion.py", "layout_engine.py",
        "local_edit.py", "excel_importer.py", "plan_ir_builder.py",
    ]

    # Exclusion patterns
    exclude_prefixes = [
        ".git/", "node_modules/", "__pycache__/", ".pytest_cache/",
        ".pipeline/", "data/", "assets/", "output/", "runtime/",
        "tmp/", "reference/", "lessons/", "tests/baselines/",
        "tests/fixtures/",
    ]
    exclude_extensions = {".md", ".log"}

    def should_exclude(rel_path):
        rel_str = str(rel_path)
        if any(rel_str.startswith(prefix) for prefix in exclude_prefixes):
            return True
        if rel_path.suffix in exclude_extensions:
            return True
        return False

    # Scan directories
    for scan_dir in scan_dirs:
        dir_path = ROOT / scan_dir
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(ROOT)
            if should_exclude(rel_path):
                continue
            violations.extend(scan_file(file_path, rel_path))

    # Scan root .py files
    for py_file in root_py_files:
        file_path = ROOT / py_file
        if file_path.exists():
            violations.extend(scan_file(file_path, Path(py_file)))

    return violations


class TerminologyGuardTests(unittest.TestCase):
    """Guard test that fails when _Avoid_ terms from CONTEXT.md appear in code."""

    def test_forbidden_terms_synced_with_context(self):
        """Verify all forbidden terms appear in CONTEXT.md _Avoid_ lines."""
        _formal_terms, avoid_terms = load_context_terms()
        missing = FORBIDDEN_TERMS - avoid_terms
        self.assertEqual(
            missing, set(),
            f"Forbidden terms not found in CONTEXT.md _Avoid_ lines: {sorted(missing)}"
        )

    def test_no_forbidden_terms_in_code(self):
        """Scan codebase and verify no forbidden terms appear."""
        violations = scan_codebase()
        if violations:
            lines = []
            for rel_path, line_num, term, snippet in violations:
                lines.append(f"  {rel_path}:{line_num}: {term} | {snippet}")
            self.fail(
                f"Found {len(violations)} forbidden term(s) in code:\n" +
                "\n".join(lines[:50]) +
                ("\n... and more" if len(violations) > 50 else "")
            )

    def test_guard_can_catch_violations(self):
        """Synthetic test: verify the guard can catch a violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("x = '智能画布'\n", encoding="utf-8")

            violations = scan_file(test_file, Path("app/test.py"))
            self.assertGreater(
                len(violations), 0,
                "Guard should have caught '智能画布' in synthetic file"
            )

            terms_found = [v[2] for v in violations]
            self.assertIn("智能画布", terms_found)

    def test_guard_catches_english_patterns(self):
        """Synthetic test: verify the guard catches English pattern violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            # log_id as a field, and "logs" as a string literal key
            test_file.write_text('data = {"log_id": "123"}\nlegacy = canvas.pop("logs", None)\n', encoding="utf-8")

            violations = scan_file(test_file, Path("app/test.py"))
            terms_found = [v[2] for v in violations]
            self.assertIn("log_id", terms_found)
            self.assertIn("logs (as history field)", terms_found)

    def test_guard_end_to_end_negative(self):
        """End-to-end negative test: write a file with a forbidden term, scan, verify violation."""
        import os
        app_dir = ROOT / "app"
        test_file = app_dir / "tmp_guard_e2e_test.py"
        try:
            test_file.write_text("# 生成日志\nx = '生成日志'\n", encoding="utf-8")
            violations = scan_file(test_file, Path("app/tmp_guard_e2e_test.py"))
            terms_found = [v[2] for v in violations]
            self.assertIn("生成日志", terms_found,
                          f"Expected '生成日志' in violations, got: {terms_found}")
        finally:
            if test_file.exists():
                test_file.unlink()


if __name__ == "__main__":
    unittest.main()
