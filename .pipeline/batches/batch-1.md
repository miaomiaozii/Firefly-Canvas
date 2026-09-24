# 任务卡 batch-1：#139 术语一致性守卫测试

## Issue 原文（照抄，不改写）

**#139 加"术语一致性"守卫测试，防止 _Avoid_ 术语再漂移**

### What's wrong

代码/i18n/测试反复出现 CONTEXT.md 的 _Avoid_ 术语（log 指代生成历史、画布误用指代项目、生成日志等），说明缺少一致性守卫，术语会持续漂移。改名只治标（清掉当前这批），不保证以后不再漂移。

### What I expected

一个"术语一致性"守卫测试，作为真根治（防再漂移）：

- 维护一份"禁止代码模式"清单，从 CONTEXT.md _Avoid_ 派生，含中→英映射（如 生成日志→历史语义的 log 标识符、画布分类→项目级 canvas 误用）。
- grep 代码/i18n/测试，命中即 fail。
- 白名单豁免：smart-* 实现标识符（见 #136）、真实 logging（console/logger/diagnostics）、核心画布/经典画布等 CONTEXT.md 定义的合法术语。
- 以后谁再引入 _Avoid_ 术语，CI 直接红。

### Steps to reproduce

1. （当前）在代码里引入任意 _Avoid_ 术语，无任何检查阻止，术语漂移无人发现
2. （期望）术语一致性测试 fail，阻止合入

### Additional context

- #115 三层根治的第 3 层（真根治：防再漂移）。
- 守卫清单需与 CONTEXT.md 的 _Avoid_ 列表保持同步（CONTEXT.md 改 _Avoid_ 时同步更新清单）。

## 验收标准（主会话确认）

1. 新增 `tests/test_terminology_guard.py`（守卫测试本体）。
2. "禁止代码模式"清单从 CONTEXT.md `_Avoid_` 派生，含中→英映射。
3. 测试 grep 代码/i18n/测试，命中禁止模式即 fail（输出 file:line + 术语 + 片段）。
4. 白名单豁免三类：smart-* 实现标识符、真实 logging（console/logger/diagnostics）、CONTEXT.md 定义的合法术语（核心画布/经典画布等）。每条白名单必须有理由。
5. **同步守卫**：测试解析 CONTEXT.md，断言禁止清单里每个术语都出现在 CONTEXT.md 的 `_Avoid_` 行中（防止清单与 CONTEXT.md 脱节）。
6. **自检**：测试必须包含一个"守卫确实能抓到违规"的合成用例（用内存字符串或临时文件验证扫描函数会报错，不依赖真实违规存在）。
7. 当前代码库（#137/#138 清理后）跑守卫测试**全绿**。
8. 负向验证：临时在扫描范围内放一个含禁止术语的文件 → 测试 fail → 删除该文件 → 恢复全绿（在 result.md 记录证据）。
9. 测试运行时间 < 10 秒。
10. 不提交 commit（主会话审核后提交）。

## 侦察提示（主会话已确认）

- **CONTEXT.md** 在仓库根目录（161 行）。术语表格式：`**术语**：` 换行描述，再换行 `_Avoid_: 词1、词2、...`（顿号分隔）。解析时注意：有些 _Avoid_ 词同时是其他术语的正式名称（如"对话节点""生成记录""局部修改""删除节点"是正式术语）——禁止清单只收录**在代码里没有合法用途**的术语；有合法用途的靠白名单或干脆不进禁止清单（判断依据：扫描命中 + 上下文）。
- **测试风格**（参照 tests/test_agent_canvas_tools.py 等）：`unittest.TestCase`，docstring 首行写 `... (issue #139)`，`ROOT = Path(__file__).resolve().parents[1]`。
- **单测命令**：`python -m pytest tests/test_terminology_guard.py -q`
- **全量回归**（#137 用过的命令）：`python -m pytest tests/ -q --ignore=tests/excel_import_browser.py`
- **扫描范围**（守卫测试应覆盖）：`app/`、`tests/`、`static/`、`CLI/`、`API/`、`tools/`、`plan_ir/` + 根目录 `.py`（main.py、firefly_*.py、layout_engine.py、local_edit.py、excel_importer.py、plan_ir_builder.py）。文本文件：.py .js .ts .json .html .css。
- **排除**：.git、node_modules、__pycache__、.pytest_cache、.pipeline、data/、assets/、output/、runtime/、tmp/、reference/、lessons/、tests/baselines/、tests/fixtures/ 下二进制、所有 .md 文件、*.log、二进制文件（读不出 utf-8 就跳过）。
- **守卫测试文件自身必须排除在扫描外**（它包含禁止术语作为数据）。
- **#137 已做的 log→history 改名**（commit 0b1e249）：`log_id→history_id`、`logs→history`（生成历史语义）、i18n 键 `canvas.generationLogs→generationHistory`、DOM id `smartLogToggle→smartHistoryToggle`。英文 log 禁止模式要能抓回潮（如 `generation_log`、`gen_log`、`historyLog`、`log_id`），但**不能误伤真实 logging**（logger、logging 模块、console.log、diagnostics、日志文件路径）。
- **smart-\***：smart-prompt / smart-image / smart-agent / smart-conversation 是生产节点类型标识符 + 已存数据（#136 待人工决策是否迁移），**必须白名单豁免**，理由写"#136 实现标识符，迁移决策未定"。
- **白名单数据格式建议**：`WHITELIST = [(相对路径, 匹配串或正则, 理由), ...]`。命中 (文件, 术语/模式) 都在白名单里才豁免。避免用行号（会漂移）。
- 如果扫描发现 #137/#138 漏掉的**明确误用**（非白名单、非正式术语），可以顺手改掉（小范围改名），并在 result.md 里单独列出；拿不准的进白名单 + 疑问记录，不要大改。
- **不要动**：docs/、*.md、CONTEXT.md、.pipeline/。
- i18n 文案主要在 static/ 的 JS 里（中文键值对），也在扫描范围内。

## 输出要求

完成后把自报结果写进 `.pipeline/batches/batch-1-result.md`：
- 改了什么（文件列表 + 每个文件一句话）
- 禁止清单最终收录了哪些术语（分：中文直接命中 / 英文模式）
- 白名单最终条目（文件 + 模式 + 理由）
- 验收标准 1-10 逐条对照（PASS/FAIL + 证据）
- 测试输出摘要（守卫测试 + 全量回归的 passed 数）
- 负向验证证据（fail 时的输出片段）
- 遗留风险 / 疑问
