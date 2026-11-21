# #404 B1：方案工具层评测

只含评测基础设施，不改产品、AGENTS 或共享 spec/plan；不涉及 #405 轨迹实现、真实模型或收费生成。

## 合同与 gold

权威原文：共享只读 `.local/agent-work/channel/review-batch-ui/MESSAGES.md:3355–3359`，01a1ef85 转述用户对 **#399** 的既有裁定；实现依据 **eb1715e `plan_ir/agent_preview.py:97`**。

- **一个主图/A+ brief = 一个任务**。多场景、四宫格、轮播、六个尺码都不自动拆分。
- 参考意向图仅作提示词描述；不自动归档、挂图、连线。建议如存在，必须 `enabled:false, usage:description_only`，任务 `refs:[]`、`selected:false`。
- **E0 = 15（主图8 + A+7），不是18**。旧 evalset/spec 中拆分和自动参考图绑定期望已全局失效，#405 亦同。
- E3/Walensee 不在任何输入清单、gold 或评测参数中。

`gold/E*.json` 是测量前按原始行文手写的期望，不由 `project_workbook`、`plan.import_excel` 或已有 probe 结果生成。原始检查使用 `XlsxParser(literal_only=True)`；E7 的原始投影受超宽表限制，人工通过 ZIP/OOXML 原文检查其图片方案。E6 V2、E8、E9 也核对了原始 OOXML 行文。只检查指定项目，没有扫描其他 Z: 项目或读取文中引用的拍摄/H33目录。

每条 literal criterion 是合取：所有指定文本片段必须原样保留；不是关键词命中率。评分还含原始行身份/顺序、总数、每表分组数量、E0 主图/A+ 分组、排除项、同主图意图、描述用途/默认不选/无附件和拆分等安全条件。E6 的空白 A+2 允许省略，若保留必须有待审核警告。原文偏差已在手写决策中记录：E7 原始 A+ 是7个 brief +1场景补充；E8 因重复主图3，原始是11+11个 brief，而非旧总数。

每次显式测量先复制全部 gold 到外部报告的 `gold-before-measurement/` 并记录 SHA256；不存在自动更新 gold 的命令。第一次测量与复测、当前 gold **9/9 字节一致**，见外部 `b1-source-and-result-audit.log`。基线不达标不更新期望。

## 只读复制

从本工树执行（UTF-8 Python 3.12）：

```text
python tools/copy_eval_fixtures.py --target D:/ff-qa/404-405-326/fixtures-v1
```

固定白名单9个原始 xlsx，加 E2 的全部3份批准修订版本（`含场景提示词`、`场景提示词修订版`、`按示例格式`），修订来源固定为 `C:/Users/Administrator/Documents/Codex/2026-11-12/new-chat/outputs`。E0 固定为 `Z:/设计/3D/2026/10月/金属雪铲V2/参考/金属雪铲V2-图片方案0910.xlsx`。仅枚举 E0 `出图/渲染`、E4 `出图`、E8 `出图/渲染图` 的直接图片；不递归发现项目、不复制成品生图、不追踪工作簿文本内路径。

目标必须全新，拒绝覆盖/合并、仓库或源目录重叠、路径穿越、重复目标、源符号链接/junction、遮罩、缺失输入和数量变化。Z: 映射到批准的 UNC 根属于正常路径解析。源只用 `rb` 打开；复制前后校验源大小/mtime/SHA，目标校验 SHA，失败删除本次不完整副本。`manifest.json` 为每个复制文件记录源绝对路径、源相对路径、目标相对路径、SHA256、字节数；缺少任何输入直接报错，不跳过。

本次实际复制 **42文件 / 610,541,225字节（582.26 MiB）**：12工作簿 +30产品渲染图，排除48个 Cryptomatte/ID遮罩；二进制不在 Git。

| 用例 | 复制文件 | 字节 | 渲染图 |
|---|---:|---:|---:|
| E0 | 7 | 67,948,347 | 6 |
| E1 | 1 | 4,956,389 | 0 |
| E2（4版本） | 4 | 103,423,243 | 0 |
| E4 | 12 | 96,095,487 | 11 |
| E5 | 1 | 2,350,905 | 0 |
| E6 | 1 | 86,878,976 | 0 |
| E7 | 1 | 30,179,351 | 0 |
| E8 | 14 | 146,779,595 | 13 |
| E9 | 1 | 71,928,932 | 0 |

成功日志 `D:/ff-qa/404-405-326/copy-fixtures-v1-02.log`；首次复制拒绝正常 Z:/UNC 映射（未创建目标），之后修正的是复制安全判定，不是素材或 gold。

## 运行方式

外部 wrapper `D:/ff-qa/404-405-326/run_pytest.py` 由父代理准备，REPO 指向本工树，ROOT 为同一个 QA 根。必须使用唯一 label；不调用全局 pytest、不接用户实例/3000。以下是本次实际命令：

```text
python D:/ff-qa/404-405-326/run_pytest.py b1-deterministic-03 tests/eval/agent_plan tests/test_agent_plan_399.py::test_copy_only_task_has_reviewable_nonempty_visual_prompt tests/test_agent_plan_399.py::test_headerless_image_count_remains_one_agent_task_but_legacy_still_splits
python D:/ff-qa/404-405-326/run_pytest.py b1-transport-01 tests/eval/agent_plan/tool_cases.py --agent-plan-fixtures D:/ff-qa/404-405-326/fixtures-v1 --agent-plan-report D:/ff-qa/404-405-326/b1-report-01
python D:/ff-qa/404-405-326/run_pytest.py b1-transport-02 tests/eval/agent_plan/tool_cases.py --agent-plan-fixtures D:/ff-qa/404-405-326/fixtures-v1 --agent-plan-report D:/ff-qa/404-405-326/b1-report-02
python D:/ff-qa/404-405-326/run_pytest.py b1-missing-fixtures-01 tests/eval/agent_plan/tool_cases.py -k E0-original-REST --agent-plan-fixtures D:/ff-qa/404-405-326/fixtures-intentionally-absent --agent-plan-report D:/ff-qa/404-405-326/b1-missing-report-01
python D:/ff-qa/404-405-326/run_pytest.py b1-default-collection-01 --collect-only
```

可用 `AGENT_PLAN_FIXTURES` / `AGENT_PLAN_REPORT` 代替两个参数。报告目录亦须全新且在外部。普通 pytest 只默认收集本目录的 **53个确定性测试**，不访问外部数据；完整默认收集验证为 **4113项，exit0**，不包含 `tool_cases.py`。完整全套执行未运行，不声称全套通过。

显式环境评测文件名为 **`tool_cases.py`**，不会默认收集。缺少清单/任何文件、哈希改变、缺失版本或产品图都导致错误，且生成 unavailable 报告。负例运行实际 **1 error /12 deselected，exit1**，无 skip、无假通过。

真实路径为：本地 `ProjectStore` + `main.app` → 附件上传 API → conversation prompt API → REST `plan.import_excel`；另测 E0 MCP。只有 host `spawn_session/send_command` 用本地桩；不替换解析/投影/附件许可/导入工具。TestClient 自持 ASGI event loop，不启动监听服务，不发模型请求。所有项目名和独立 store 子目录均用 `e2e-tabbit-` 前缀；每例结束关闭生命周期、释放会话 registry、清理本 store 的预览 cache 并删除自己的项目目录。

## 裁定后 gold v2（#404）

`MESSAGES.md:3379–3382` 明确非空 `suggested_refs` 不是字面合同；移除 E0 的额外硬性条件，其他行文/安全断言保留。旧 gold、红测及原评分不覆盖。其他 case 的非空建议项只是可见性基线观测，不是必须全过的字面合同门。

独立复测：`b1-approved-15-01` 新增确定性 **56 passed /1.77s**；真正的工具层另跑 `b1-approved-15-e0` **2 passed /11 deselected /2.43s**，E0 REST/MCP各 **42/42**，仍15任务。修正后前测快照与回包在 `D:/ff-qa/404-405-326/b1-approved-15-report/`。目录和显式子文件同次传给 pytest 会去重、漏收非默认文件名，故命令必须分开，不能把56项当成工具层通过。

#470 独立修复表头别名，之后另补 E0 行3精确两图的正向 gold；这是新授权的可见性回归，不恢复原先未经批准的非空硬门。原裁定记录见 `docs/design/agent-plan-evaluation-contract-399.md`。

## #470 后的 gold v3（当前工具层结果）

#470 commit `acbe405` 只改 `header_detector` 与回归测试。新增有限别名：`示例图` 来自 E0 实际表头；`意向图` 来自 #399/#470 的业务术语；`示意图` 作为该术语的常见同义表达。不把“示例/示意/意向”单字样泛化为图列。原别名/顺序不变；同一组别名进入检测计分、精确列名与映射阶段。

- `alias470-red-real`：6 failed /11 passed；3个检测别名和3个真实投影的两图条件失败。更早 `alias470-red` 含本次新测试的二维行构造错误，原日志保留，不将那3个错误充当产品红测。
- `alias470-green`：新回归 +既有 builder/#399 共132 passed /4.89s；断言15任务、物理行3精确两图、禁用/description_only、零refs/零路径、其余行无建议图。
- gold 现在按 `MESSAGES.md:3381` 对 E0 行3增加 **list length=2** 正向回归；不能接受仅非空、多图或非列表。新增反例/非法gold验证后，`b1-count2-deterministic` 68 passed /1.81s。
- `b1-count2-corpus` 真实全夹具为 **10 passed /3 failed /5.33s**（传输测试）；E0 REST/MCP 各 **43/43**，准确15任务、行3两张禁用描述图；完整回包、逐项评分与新的前测快照在 `b1-count2-corpus-report/`。E1仍15/23、E4仍22/23、E5仍10/18；E6/E9附件413、E7导入NOT_XLSX。未改这些产品行为，不声称数据集全绿。

## 首次冻结 gold v1 的基线结果（历史，不是全绿验收）

以下保留裁定前原始状态；其中“未提交/不能宣告成功/等待裁定”等句只代表当时，不是当前结论。

确定性：**53新增 +2条既有 #399 回归 =55 passed**，exit0。

每次真实评测共 **13项（12 REST +1 E0 MCP）**；**10个实际导入成功，3个传输错误**。Pytest 是 **8 passed /5 failed，exit1**：两个 E0 门失败 +三个传输失败；8个 harness pass **不代表8个语义全通过**。两次测量语义结果相同。复测 E0 的 REST/MCP literal preview 去除会话身份后完全相同，`rest_mcp_parity:true`。

| 用例/版本 | 通过条件/总数 | 语义率 | 实际任务 | 状态 |
|---|---:|---:|---:|---|
| E0 REST、MCP 各 | 42/43 | 97.7% | **15** | **门失败** |
| E1 | 15/23 | 65.2% | 32 | 基线不达标 |
| E2 original | 24/24 | 100% | 15 | 条件全通过 |
| E2 scene / revised / example 各 | 27/27 | 100% | 15 | 条件全通过 |
| E4 | 22/23 | 95.7% | 22 | 基线不达标 |
| E5 | 10/18 | 55.6% | 5 | 基线不达标 |
| E6 | 0/29 | 不可用 | — | 附件 API 413，86,878,976 >67,108,864字节 |
| E7 | 0/25 | 不可用 | — | 导入 API 400 `NOT_XLSX` |
| E8 | 24/24 | 100% | 22 | 条件全通过 |
| E9 | 0/24 | 不可用 | — | 附件 API 413，71,928,932 >67,108,864字节 |

**E0 精确阻断项：** `literal31:图片方案-金属雪铲!3:suggested_refs`；手写期望为非空描述用途建议，实际为 `[]`。原始工作簿确有该物理行浮动图锚点 `(row3,col3)`、`(row3,col4)`（列从0起），独立原始检查记录在 `b1-source-and-result-audit.log`。其余42项全部通过，包括准确15任务、8+7分组、原始行序、逐行文本、同主图意图、不拆分、不选、不挂图。**没有为通过而删除/迁移此检查，也没有改产品**。父代理需处理此门，不能把 #404 写成 E0 all-pass。

E1 续行未合并、备注备选文案丢失；E4 描述建议缺失；E5 无表头分段与4个 brief不符，均保留为基线失败。E6/E9 不增大产品上限、不裁剪/重写原表绕过真实附件 API；E7 不删除噪声表或放宽解析限制。

## 父会话独立复核（裁定前历史）

依据 `MESSAGES.md:3355–3359`，共享 spec/plan 与当前 worktree 的忽略目录镜像已同步15项合同；可提交依据在 `docs/design/agent-plan-evaluation-contract-399.md`。

- 复制器与 manifest 同时拒绝 POSIX/Windows 的盘符、绝对与父级路径；新增安全负例后，`b1-parent-deterministic-01` **56 passed /1.85s/exit0**。这是最新新增确定性测试结果，不包括先前额外两条既有测试。
- `b1-parent-e0-01` 再次跑真实 REST/MCP，**2 failed /11 deselected /2.73s/exit1**；两条均为同一非空 `suggested_refs` 条件，其他各42项通过。完整回包/前测gold在 `b1-parent-e0-report/`，不能报 E0 all-pass。
- 此非空字段是手写 gold 对“意向图只用于提示词”的额外解释，不是 review 已批准的逐字段要求。当前保持 gold 不变，请 review 明确它是否属合同；若属产品缺口，需要另票/授权，不混入仅测试范围。
- 只读追踪 `e0-image-trace.json`：E0 的“示例图”表头未被识别为 `operationRef`；两张行3浮动图及字节均存在。只给字段映射副本补该别名时，同一 resolver 返回两图。诊断没有改变真实API回包、产品或评分，不能当通过。
- `D:/ff-qa/404-405-326/BLOCKER-E0-SUGGESTIONS.md` 保存合同问题、精确失败、基线与重跑路径。未提交 #404，未开始 #405/#326，未跑交付 full/fast。

## 首次测量证据与清理（历史）

统一根：`D:/ff-qa/404-405-326/`。

- `b1-report-02/report.json`、`report.md`：逐条件期望/实际、每case汇总、E0门和不可用原因；`*-actual.json` 为真实API回包。
- `b1-report-01/` 保留首轮；`b1-report-02/gold-before-measurement/` 与首轮 gold v1 字节一致；v2/v3 的 E0 裁定修改各有独立快照，不能与当前文件混同；`fixture-manifest.json` 保存复制来源与哈希。
- `runs/<label>/command.json`、`pytest.log`、`pytest.xml`、`exit.txt`：精确命令、HEAD、隔离目录、原始结果。早期确定性 `b1-deterministic-01` 为42通过1失败；`b1-deterministic-02` 为44通过；子代理结束时为上述55通过，随后父会话56项结果见上节。
- 安全负例暴露 Windows `/absolute.xlsx` 是 rooted 而非 fully absolute 路径：初次测试曾生成 `D:/absolute.xlsx`（30字节合成内容），核验内容后已删除；复制器改为拒绝 `anchor/drive`，新增对应回归。未改任何用户文件。这是唯一意外范围外临时文件。
- 两次传输结束时独立 projects 根均为空；wrapper 的本次 runtime/projects/tmp/home 等数据根在留存日志后删除；只保留外部 fixtures、报告、命令和测试日志。不清理父代理既有 probe/fixtures。
- 产品 shutdown 每例有一条无条件 `unresolved cleanup remains guarded` 警告（`api_agent_tools.py:121`）；未修改/屏蔽产品日志，不据此声称恢复路径已验证。
- 该子代理移交时未启动真实LLM、图像/视频生成或监听服务，未接入其他实例，未 commit/push/post；父会话后续提交和测量以上述 v2/v3 段为准。
