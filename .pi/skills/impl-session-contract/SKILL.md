---
name: impl-session-contract
disable-model-invocation: true
description: 交付方与验收监理的交接协议。Use when 准备关闭某个 Issue 或打 ready-for-agent、收到 subject 含「验收打回」的邮件、要向监理提交一次交付。
---

# 交付方交接协议

你（含你派出的子代理）是交付方。判定权在监理（`.pi/skills/acceptance-supervisor/SKILL.md`）：它自己复跑门禁，你的汇报是索引，不是证据。

## 上岗档位

默认 `{REASON:007}{REASON:icode}`（agentic 操作员：多步工具编排 + 错误隔离 + 改→跑门禁→修到绿的自纠错循环），后续每条重复同一组合。选型是 2026-11-10 两轮六组合×两任务实测定的（见 `.pi/skills/reasoning-modes/SKILL.md`）；备选 `{REASON:ultra}{REASON:icoder}`，阶段拆分最清楚、“先提交再汇报”说得最明，但实测两轮都把正文写到截断，用它就需把输出预算提到 4000+。

**不加 `x` 前缀**：`007`、`house`、`musk`、`stark`、`coder` 都没有 x 版，写 `{REASON:x007}` 是无效令牌且不报错；`xultra` 实测会把正文挤空。`house` 不做默认档位：实测它把 House 剧本人格带进汇报（“蠢bug”“别让我再说第二遍”、医学比喻），还漏了“先提交”这一步。

**harness 档位用 `--thinking xhigh`**。`--thinking high` 对这几套模型是 `null`（`thinkingLevelMap` 里 high 与 max 为空），等于没设。`max_tokens` 单条上限 32768，重令牌实测单条能吃掉 11000+ token、单题 5–9 分钟（`xultra` 不存在，别写；要重就用 `xhyper`/`xlogic`）。先确保四段汇报能完整写完，再谈更重的档位。

## 提交一次交付

1. **先锁约束**：开工前把 Issue 正文与现有测试里写死的值（端口、阈值、档位名、目录名）列成一份约束清单，贴进计划。改动前先比对——实测两个档位组合都因为凭直觉改端口值，把场景弄挂而不自知。
2. **先提交**：改动提交到 issue 分支并记下 commit hash——未提交的改动监理验不了，也会被别的会话 `git clean`/`checkout` 清掉。`git status --porcelain` 必须为空，未跟踪文件也算脏。
3. **自查**：`python tools/quality_gate.py fast --repo-root <工作树>`；交付前跑 `trial`（300 s，累计超集：跑 trial 就不用再跑 fast/domain），证据归档到监理指定的 `EVIDENCE_ROOT`。新增红项清零才算可提交。**改前端或 agent 宿主还要跑端到端**：`bash tools/e2e_qa.sh --shots-dir <EVIDENCE_ROOT>/<issue>/shots --out-json <EVIDENCE_ROOT>/<issue>/e2e.json`（截图基线比对：`python tools/e2e_shot_diff.py --evidence-dir … --baseline-dir …`）。**按改动范围跑对应场景脚本**：`tools/e2e/scenario_delete_undo.sh`（撤回链）、`scenario_mouse.sh`（鼠标）、`scenario_keys.sh`（快捷键）、`scenario_ux_rules.sh`（体验规则）——它们没接入门禁，不跑就是没证据。
4. **汇报**：

   ```bash
   orca orchestration send --to run:<监理的 Run> \
     --subject "#<issue> 待验收" --body <正文>
   ```

   监理的 Run 由维护者在激活时给你。你自己这一侧：`orca orchestration run-current --json`，没有就 `orca orchestration run-create --objective "delivery #<N>" --json`——监理打回要靠这个 Run 找到你。邮件由 Orca 主动送达，不要写脚本轮询 `check`（它会消费邮件）。
5. **关闭**：等监理签字。签字后由你按 `docs/agents/issue-tracker.md` 写结论评论并关闭 Issue，triage 标签由监理落。

汇报里另加两行：①**改动清单逐项对照 Issue 声明**（改了哪些文件，各自对应 Issue 里哪一条；Issue 没声明的改动要么删除要么先补声明）；②**断言与场景改动单独自证**：改动或新增 `tests/**` 与 `tools/e2e/**` 断言时，写清断的是什么行为终态、为什么不是放宽旧断言；修飘报测试时先给复现统计（跑 20 次挂几次），没复现就说没复现，不得先改断言。

**完成判定**：改动已提交、四段齐全（含末尾通俗解释）、证据落在 `EVIDENCE_ROOT`、Issue 状态与监理裁定一致。汇报正文为空即未完成——重档位下模型可能把预算全花在思考通道，加大预算或降档重发。

## 什么时候停下来问

满三种就停，不要自己拍：

- **验收口径本身成了待改对象**（要改断言、改门禁、改 Issue 里的验收标准）——这属于改考卷，只有维护者能批。
- **Issue 未声明的改动变得必要**（如要改 `tests/**` 既有断言、`tools/quality_gate.py`、`tools/e2e/**`、`package*.json`）——先补声明与理由，再动。
- **出现产品取舍或两种都能过的方案**——转给监理，由它升级维护者。

停下来时发一封 `--subject "#<issue> 待决断"` 的邮件，列出 2–4 个选项及各自代价，等回复再动。

## 收到打回

按维护者指令处理：只修打回列出的项 → 重跑自查 → 汇报标"第 N 轮 + 本轮改了什么"。两轮未过就问监理要不要升级。验收口径不是交付方可改的对象。

## 让改动经得起一票否决

- 改动 `tools/quality_gate.py`、`tests/**` 断言、`tools/e2e/**` 场景断言、`.github/**`、`package*.json` 时：先让 Issue 声明这项改动，并在汇报里单列一行说明为什么非改不可。
- "按钮已存在"回答不了"功能是否可用"：按六维验收面的口径回答（Issue #294）。
- 汇报里只写本会话真跑过的命令与真拿到的结果。
