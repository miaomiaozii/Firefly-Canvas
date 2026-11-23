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
    # #363：启动失败路径要读启动日志尾巴，这里的 'logs' 是 #364 定的日志目录名（真日志，不是画布历史）
    ("tools/launch-firefly.ps1", "logs (as history field)", "Boot log directory path (#364), real logging not history"),
    ("tools/wait-for-local-server.ps1", "logs (as history field)", "Boot log directory path (#363), real logging not history"),
    ("firefly_diagnostics.py", "logs (as history field)", "Diagnostics log directory path"),
    ("firefly_projects.py", "logs (as history field)", "Legacy logs key compatibility"),
    # fal.ai 队列协议的上游响应字段：status 返回体的 logs 是服务商契约字段名，不是画布历史字段
    ("app/services/generation.py", "logs (as history field)", "fal queue status response field (upstream contract)"),
    ("tests/test_fal_queue_image_mode.py", "logs (as history field)", "fal queue ERRORED status fixture mirroring upstream field"),
    # File-level whitelists for terms with few hits
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


LOGS_PATTERN = "logs (as history field)"
REMOVED_RUNTIME_MARKER = "// Local ComfyUI generation execution was removed."

# Exact path + term + stripped source line. Neither a whole adapter file nor
# every use of a spelling is exempt: new text in the same file still gets scanned.
SCOPED_EXCEPTIONS = {
    # #331 (ff7bcfb, ADR 0025): the actual provider, not a synonym for online generation.
    ("app/core.py", "ComfyUI"): {
        "# ---- #331 本地 ComfyUI（MiniMax-H3-Image GGUF）----",
        "# 画布直接把本地 ComfyUI 当生图后端：POST /prompt → GET /history/{id} → GET /view。",
        "# #331：ComfyUI 的请求形状（/prompt 图 + /history 轮询）是协议内生的，不允许逐模型改协议。",
        "# #331：本地 ComfyUI 不需要 API Key，Base URL 缺省指向本机 8188。",
        "# #331：本地 ComfyUI 的工作流是固定的，给一个能直接命中的模型名，省得用户先猜。",
    },
    # Registry provenance only; #373 does not decide whether display_name may show the provider.
    ("app/data/model_specs.json", "ComfyUI"): {
        '"source": "本地 ComfyUI 实测（ComfyUI 0.45.2 + ComfyUI-GGUF，MiniMax-H3-Image Pruned Q4_K_M，RTX 4070 Ti SUPER），见 issue #331"',
    },
    # The probe implementation is distinct from message copy rendered in settings (#373).
    ("app/routers/api_providers.py", "ComfyUI"): {
        '"""ComfyUI 没有 /v1/models，改探 /system_stats + 关键节点是否存在。"""',
    },
    # d040452: provenance of the upstream edit template and prompt expansion.
    ("app/services/comfyui_pe_prompts.py", "ComfyUI"): {
        "逐字取自 ComfyUI 官方模板 `templates/image_minimax_h3_image_edit.json` 的",
    },
    ("app/services/comfyui_workflows.py", "ComfyUI"): {
        '"""本地 ComfyUI（MiniMax-H3-Image GGUF）工作流模板与尺寸映射。',
        "#331：画布把本地 ComfyUI 当成一个生图服务商协议 `comfyui`：提交 API 格式的图、",
        "图结构取自实测可用的 9 节点模板（ComfyUI 0.45.2 + ComfyUI-GGUF）：",
        "# 实测跑成 3 token/s、单张能耗掉 15~23 分钟（ComfyUI 日志：8196 上限跑到 43% 就 19 分钟）。",
        '"""把画布的比例 + 面积档换算成 ComfyUI 要的 width/height（8 的倍数）。',
        "ComfyUI 内部的 latent 对齐是 16，官方模板多对齐到 32 是为了让 VAE 每边张量能被",
    },
    # Adapter implementation and task errors naming the actual execution service.
    # ADR 0025/#331 (ff7bcfb, 2174d4d, cec4f0b) keeps these provider-specific diagnoses.
    ("app/services/generation.py", "ComfyUI"): {
        '"""ComfyUI 根地址。它是会话式多端点接口（/prompt、/history、/view、/upload/image、/queue），',
        '"""参考图 → ComfyUI 上传载荷。返回 (payload, (w, h))；payload 为 None 时 w/h 为 (0, 0)。"""',
        '"""把还没开始跑的任务从 ComfyUI 队列里撤掉。',
        "只删队列不跑 `/interrupt`：ComfyUI 是单队列，interrupt 会打断**正在跑的**那一张（可能是用户别的任务），",
        '"""ComfyUI 显存不足。这不是参数错，也不是随机失败：调用方释放显存后可以且应该重跑一次。"""',
        '"""让 ComfyUI 卸载模型并释放缓存显存（POST /free）。失败无所谓，只是尽力。"""',
        "f'ComfyUI 生图超时（已等 {int(now - started)} 秒，prompt_id={prompt_id}），已从队列撤单。'",
        "'若产物已落盘，可在 ComfyUI 的 output/MiniMax-H3-Image/ 目录里找到；本适配器不自动重提。'",
        "raise HTTPException(status_code=502, detail=f'查询 ComfyUI 任务状态失败：{exc}') from exc",
        "f'ComfyUI 执行失败：{raw_error}'",
        "'（显存不够：已让 ComfyUI 卸载模型腾出显存重跑一次仍失败，'",
        "raise HTTPException(status_code=502, detail=f'ComfyUI 执行失败：{raw_error}')",
        "\"\"\"把画布参考图送进 ComfyUI 的 input 目录，返回 (名字, 缩放后尺寸)。解析不到返回 ('', (0, 0))。\"\"\"",
        "raise HTTPException(status_code=502, detail=f'ComfyUI 参考图上传失败（{upload.status_code}）：{upload.text[:200]}')",
        "raise HTTPException(status_code=502, detail=f'ComfyUI 参考图上传未返回文件名：{str(raw)[:200]}')",
        '"""本地 ComfyUI（MiniMax-H3-Image GGUF）文生图 / 图生图。',
        "一次画布任务 = 一张 ComfyUI 图：POST /prompt → 轮询 /history → GET /view。",
        "'参考图无法交给本地 ComfyUI：只支持项目媒体、应用本地图片、data URL 与公网 http(s) 链接。'",
        "raise HTTPException(status_code=503, detail=f'连不上本地 ComfyUI（{root}）：请确认 ComfyUI 已启动，或修改该服务商的 Base URL。') from exc",
        "raise HTTPException(status_code=504, detail=f'提交 ComfyUI 任务超时（{root}），任务可能已入队，请先到 ComfyUI 队列确认再重试。') from exc",
        "raise HTTPException(status_code=502, detail=f'ComfyUI 拒绝该工作流：{detail}')",
        "raise HTTPException(status_code=502, detail=f'ComfyUI 未返回 prompt_id：{str(submitted)[:300]}')",
        "# 让 ComfyUI 卸掉模型、释放缓存，然后同图重跑一次——只重试这一次，第二次还不行就把错抛出去。",
        "# ComfyUI 的 ResolutionSelector 会把边长对齐到它自己的步长（实测 16:9/1k 要 2728 实际出 2720），",
        "raise HTTPException(status_code=502, detail=f'从 ComfyUI 取图失败（{view.status_code}）：{image.get(\"filename\")}')",
        "raise HTTPException(status_code=502, detail='ComfyUI 返回了空图片。')",
        "# #331：本地 ComfyUI 直接吃画布的 aspect_ratio + resolution 档；显式尺寸走适配器内的 8 倍数换算。",
        # #472: real provider diagnostics, never a synonym for online generation.
        "\"\"\"ComfyUI 提交不幂等；除已确认的 OOM 腾显存一次外，不自动重提。\"\"\"",
        "'若产物已落盘，可在 ComfyUI 的 output/MiniMax-H3-Image/ 目录里找到；本适配器不自动重提。',",
        "detail += '，任务可能已入队，请先检查 ComfyUI 队列再决定是否重试；本适配器不自动重提。'",
        "detail += '，任务可能已入队，请先检查 ComfyUI 队列；本适配器不自动重提。'",
        "detail += '，请检查 ComfyUI output/MiniMax-H3-Image/ 目录；本适配器不自动重提。'",
        "detail = f'ComfyUI {stage}网络请求失败'",
        "detail = f'ComfyUI {stage}超时'",
        "error = _comfyui_error(f'ComfyUI {stage}{detail}（HTTP {status}）。', public_status, prompt_id=prompt_id)",
        "error.detail = f'ComfyUI 提交失败（HTTP {submit.status_code}）：{error.detail}'",
        "f'ComfyUI 生图总等待超时（已等 {int(now - started)} 秒，prompt_id={prompt_id}）。'",
        "raise _comfyui_error('ComfyUI 参考图上传响应未返回文件名。')",
        "raise _comfyui_error('ComfyUI 取图返回了空图片。', prompt_id=prompt_id)",
        "raise _comfyui_error('ComfyUI 提交响应未返回 prompt_id，任务可能已入队，请先检查 ComfyUI 队列；本适配器不自动重提。', uncertain=True)",
        "raise _comfyui_error(f'ComfyUI {context}响应不是 ComfyUI history 数据（可能是 HTML 或无效 JSON），请检查 Base URL。', prompt_id=prompt_id) from None",
        "raise _comfyui_error(f'ComfyUI {stage}响应不是 ComfyUI 数据（可能是 HTML 或无效 JSON），请检查 Base URL。',",
        "raise _comfyui_redact_error(HTTPException(status_code=502, detail=f'ComfyUI 拒绝该工作流：{detail}'), provider_key)",
        "return _comfyui_error(f'ComfyUI {stage}连接失败：无法建立连接，请确认 ComfyUI 已启动、网络可达及 Base URL 正确。', 503, prompt_id=prompt_id)",
    },
    ("firefly_generation.py", "ComfyUI"): {
        "# #331: 本地 ComfyUI 是工作流图接口，同理不允许被改成 REST 协议。",
    },
    # ADR 0004 preserves removal of the embedded runtime; #454 (eb5ab71) still uses the slice marker.
    ("static/js/smart-canvas.js", "ComfyUI"): {REMOVED_RUNTIME_MARKER},
    ("tests/canvas_module_units/test-canvas-generation-tasks.mjs", "ComfyUI"): {
        f"const end = source.indexOf('{REMOVED_RUNTIME_MARKER}', start);",
        f"const end = smart.indexOf('{REMOVED_RUNTIME_MARKER}', start);",
    },
    # Fixtures and assertions refer to the named service and its provider contract.
    ("tests/fixtures/provider_contracts/supported-image-request-families.json", "ComfyUI"): {
        '"ComfyUI /prompt, /history/{prompt_id}, /view, /upload/image and /queue HTTP routes"',
        '"request_shape": "ComfyUI API-format graph POSTed to /prompt, polled via /history/{prompt_id}, image fetched from /view; local references uploaded first to /upload/image",',
        '"auth_header": "none (ComfyUI runs on 127.0.0.1)",',
    },
    # 69111cf: named fake provider for the canvas size-payload contract.
    ("tests/test_comfyui_canvas_payload.py", "ComfyUI"): {
        '"""端到端（假 ComfyUI）：画布各种 payload 变体都要落到节点 4 的正确尺寸。"""',
        "'id': PROVIDER_ID, 'name': 'ComfyUI（本地）', 'base_url': 'http://127.0.0.1:8188',",
    },
    # #472: upstream payload/diagnostic assertions; other text in this file stays guarded.
    ("tests/test_comfyui_generation_472.py", "ComfyUI"): {
        "('/prompt', httpx.Response(200, json=[]), 502, '不是 ComfyUI', None),",
        "('/prompt', httpx.Response(200, text='<html>login</html>'), 502, '不是 ComfyUI', None),",
        "('/upload/image', httpx.Response(200, json=[]), 502, '不是 ComfyUI', [REFERENCE]),",
        "(httpx.Response(200, content=b'{broken'), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json=None), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json=[]), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={'error': 'wrong endpoint'}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: []}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: {'outputs': {'9': {'images': [None]}}}}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: {'outputs': {'9': {'images': [{'filename': 'result.png', 'metadata': []}]}}}}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: {'status': [], 'outputs': {}}}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: {'status': {'status_str': 'error', 'messages': [['execution_error', []]]}}}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, json={PROMPT_ID: {'status': {}, 'outputs': []}}), 502, '不是 ComfyUI'),",
        "(httpx.Response(200, text='<html>login</html>', headers={'content-type': 'text/html'}), 502, '不是 ComfyUI'),",
        "assert 'ComfyUI 提交' in error.value.detail",
        "assert 'ComfyUI' in error.value.detail",
        "assert '已让 ComfyUI 卸载模型' not in error.value.detail",
    },
    ("tests/test_comfyui_h3_image.py", "ComfyUI"): {
        '"""#331 本地 ComfyUI（MiniMax-H3-Image GGUF）接入 TDD。',
        "事实来源：维护者本地 ComfyUI 0.45.2 + ComfyUI-GGUF 实测（2026-11-14），见 issue #331。",
        "'name': 'ComfyUI（本地）',",
        '"""最小可用的 ComfyUI 假服务器：/prompt、/history/{id}、/view、/upload/image、/queue。"""',
        "# 8MP 档默认 -> 1K 档 2048²；宽高以 ComfyUI 产物元数据为准（节点会对齐到自己的步长）",
        "self.assertIn('ComfyUI', ctx.exception.detail)",
        "self.assertEqual(len(fake.requests_to('/free', 'POST')), 1, 'OOM 之后必须先让 ComfyUI 卸模型释放显存')",
        "self.assertIn('参考图无法交给本地 ComfyUI', ctx.exception.detail)",
        '"""超时/取消必须撤单：ComfyUI 是单队列，一张卡死的图会堵住用户后面所有任务。"""',
        "self.assertEqual(len(deletes), 1, '超时后必须把任务从 ComfyUI 队列里删掉')",
    },
    ("tests/test_global_settings.py", "ComfyUI"): {
        "# 设置页协议下拉里的 `comfyui（本地）` 是故意保留的——用户实际在用自己的 ComfyUI",
    },
    # #364 (4408690): identifies the upstream generating the diagnostic traffic.
    ("tests/test_server_logging.py", "ComfyUI"): {
        "约 10 小时 1.37MB 里大半是轮询 ComfyUI ``/queue`` 的 httpx INFO，中文全是 GBK 乱码。",
    },
    # 2174d4d (#331): these are remote URLs, explicitly not project media originals.
    ("app/services/generation.py", "外链"): {
        "现在全部走 store / 本地媒体解析，只有真正的外部 http(s) 外链才允许下网。",
        "# 本地解析不到的才考虑外部外链。本地回环地址一律不走网：那是没解析成功的项目媒体句柄，",
    },
    # #360 (00352b9): status text for two local fake-provider fixtures, not product UI.
    ("tools/e2e/scenario_delete_undo.sh", "本地生成"): {
        "return {skip: 'KNOWN-GAP: 无本地生成失败夹具；未发送生成请求，未验证占位卡终态写回'};",
        "return {skip: 'KNOWN-GAP: 无本地生成成功夹具；未发送生成请求，未验证成功写回'};",
    },
    # #421 (d111f625), #364 (23310c5) and #458 (2f0d553): actual log directories, not history keys.
    ("app/services/agent_model_capabilities.py", LOGS_PATTERN): {
        '"sqlite_home=" + _toml_value(str(folder / "sqlite")), "log_dir=" + _toml_value(str(folder / "logs"))]',
    },
    ("tests/test_server_logging.py", LOGS_PATTERN): {
        'self.assertEqual(directory, Path(tmp) / "logs")',
        'self.assertTrue(str(directory).endswith(os.path.join("logs")))',
    },
    ("tools/firefly-boot-logs.ps1", LOGS_PATTERN): {
        "return [IO.Path]::GetFullPath((Join-Path $runtimeRoot.Trim() 'logs'))",
    },
    ("tools/refresh_cache.py", LOGS_PATTERN): {
        '_SKIP_DIRS = {"node_modules", "data", "assets", "output", "logs", "tmp", "venv", "browser-profiles"}',
    },
    # #488: This excluded diagnostics directory is not a history field; only this exact line is exempt.
    ("app/backend_source.py", LOGS_PATTERN): {
        '_SKIP_DIRS = {"__pycache__", "node_modules", "data", "assets", "output", "logs", "tmp", "runtime", "projects", "venv", "browser-profiles"}',
    },
}

# #373 (ae997cb, ADR 0004 revision): remove only the exact option inside the
# protocol selector for the ComfyUI check, not the other options or surrounding copy.
COMFYUI_PROTOCOL_OPTION = re.compile(
    r'(<select data-field="protocol">\s*(?:<option\b[^>]*>[^<]*</option>\s*)*?)'
    r'<option value="comfyui">ComfyUI（本地）</option>'
    r'(?=\s*(?:<option\b[^>]*>[^<]*</option>\s*)*</select>)'
)


def is_whitelisted(rel_path_str, term, line):
    """Check if a term in a file is whitelisted."""
    # Normalize path separators for cross-platform matching
    normalized_path = rel_path_str.replace("\\", "/")
    if line.strip() in SCOPED_EXCEPTIONS.get((normalized_path, term), ()):
        return True
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

    path = rel_path.as_posix()
    for i, line in enumerate(content.splitlines(), 1):
        for term in FORBIDDEN_TERMS:
            if term not in line or is_whitelisted(path, term, line):
                continue
            if term == "ComfyUI" and path == "static/js/global-settings.js":
                if term not in COMFYUI_PROTOCOL_OPTION.sub(r"\1", line, count=1):
                    continue
            violations.append((rel_path, i, term, line.strip()[:80]))

        for pattern_name, pattern in FORBIDDEN_PATTERNS.items():
            if re.search(pattern, line) and not is_whitelisted(path, pattern_name, line):
                violations.append((rel_path, i, pattern_name, line.strip()[:80]))

    return violations


def scan_codebase(root=ROOT):
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
        dir_path = root / scan_dir
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(root)
            if should_exclude(rel_path):
                continue
            violations.extend(scan_file(file_path, rel_path))

    # Scan root .py files
    for py_file in root_py_files:
        file_path = root / py_file
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

    def test_exact_exceptions_do_not_hide_new_uses_in_same_file(self):
        cases = [
            ("app/services/generation.py", "ComfyUI", "f'ComfyUI 执行失败：{raw_error}'", 'label = "ComfyUI 生图模式"'),
            ("app/services/generation.py", "ComfyUI", "detail = f'ComfyUI {stage}超时'", 'label = "ComfyUI 生图模式"'),
            ("tests/test_comfyui_generation_472.py", "ComfyUI", "assert 'ComfyUI' in error.value.detail", 'label = "ComfyUI 生图模式"'),
            ("app/services/generation.py", "外链", "现在全部走 store / 本地媒体解析，只有真正的外部 http(s) 外链才允许下网。", 'label = "项目媒体原件：外链"'),
            ("tests/test_server_logging.py", LOGS_PATTERN, 'self.assertEqual(directory, Path(tmp) / "logs")', 'history = data["logs"]'),
            ("app/backend_source.py", LOGS_PATTERN, '_SKIP_DIRS = {"__pycache__", "node_modules", "data", "assets", "output", "logs", "tmp", "runtime", "projects", "venv", "browser-profiles"}', 'history = data["logs"]'),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "source.txt"
            for path, term, allowed, forbidden in cases:
                with self.subTest(path=path, term=term):
                    test_file.write_text(f"{allowed}\n{forbidden}\n{allowed} # {forbidden}\n", encoding="utf-8")
                    hits = scan_file(test_file, Path(path))
                    self.assertEqual([(line, name) for _, line, name, _ in hits], [(2, term), (3, term)])
                    other_hits = scan_file(test_file, Path(f"other/{path}"))
                    self.assertEqual([(line, name) for _, line, name, _ in other_hits], [(1, term), (2, term), (3, term)])

    def test_protocol_option_does_not_hide_other_settings_copy(self):
        option = '<option value="comfyui">ComfyUI（本地）</option>'
        picker = f'<select data-field="protocol">{option}</select>'
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "settings.js"
            test_file.write_text("\n".join([
                picker,
                f"{picker}<label>ComfyUI 生图模式</label>",
                f'<select data-field="engine">{option}</select>',
                f'<select data-field="protocol">{option}<option value="other">ComfyUI</option></select>',
                f"{picker}<label>普通画布</label>",
                f'<select data-field="protocol">{option}{option}</select>',
            ]), encoding="utf-8")
            hits = scan_file(test_file, Path("static/js/global-settings.js"))
            self.assertEqual([(line, name) for _, line, name, _ in hits], [
                (2, "ComfyUI"), (3, "ComfyUI"), (4, "ComfyUI"), (5, "普通画布"), (6, "ComfyUI"),
            ])

    def test_guard_end_to_end_negative(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = Path("app/tmp_guard_e2e_test.py")
            (root / path.parent).mkdir()
            (root / path).write_text("# 生成日志\nx = '生成日志'\n", encoding="utf-8")
            violations = scan_codebase(root)
            self.assertEqual([(rel, line, term) for rel, line, term, _ in violations], [
                (path, 1, "生成日志"), (path, 2, "生成日志"),
            ])


if __name__ == "__main__":
    unittest.main()
