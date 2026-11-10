---
name: impl-session-contract
disable-model-invocation: true
description: 交付方与验收监理的交接协议。Use when 准备关闭某个 Issue 或打 ready-for-agent、收到 subject 含「验收打回」的邮件、要向监理提交一次交付。
---

# 交付方交接协议

你（含你派出的子代理）是交付方。判定权在监理（`.pi/skills/acceptance-supervisor/SKILL.md`）：它自己复跑门禁，你的汇报是索引，不是证据。

## 上岗档位

首条消息最前面带 `{REASON:007}{REASON:icode}`（多步执行 + 技术精确），后续每条重复同一组合；档位选择与禁用项见 `.pi/skills/reasoning-modes/SKILL.md`。换档位必须重开会话，并把新 Run 告知监理。

## 提交一次交付

1. **先提交**：改动提交到 issue 分支并记下 commit hash——未提交的改动监理验不了，也会被别的会话 `git clean`/`checkout` 清掉。
2. **自查**：`python tools/quality_gate.py fast --repo-root <工作树>`；交付前跑 `trial`（300 s），证据归档到监理指定的 `EVIDENCE_ROOT`。新增红项清零才算可提交。
3. **汇报**：

   ```bash
   orca orchestration send --to run:<监理的 Run> \
     --subject "#<issue> 待验收" --body <正文>
   ```

   监理的 Run 由维护者在激活时给你。你自己这一侧：`orca orchestration run-current --json`，没有就 `orca orchestration run-create --objective "delivery #<N>" --json`——监理打回要靠这个 Run 找到你。邮件由 Orca 主动送达，不要写脚本轮询 `check`（它会消费邮件）。
4. **关闭**：等监理签字。签字后由你按 `docs/agents/issue-tracker.md` 写结论评论并关闭 Issue，triage 标签由监理落。

**完成判定**：改动已提交、四段齐全（含末尾通俗解释）、证据落在 `EVIDENCE_ROOT`、Issue 状态与监理裁定一致。汇报正文为空即未完成——重档位下模型可能把预算全花在思考通道，加大预算或降档重发。

## 收到打回

按维护者指令处理：只修打回列出的项 → 重跑自查 → 汇报标"第 N 轮 + 本轮改了什么"。两轮未过就问监理要不要升级；验收口径本身不是可改的对象。

## 让改动经得起一票否决

- 改动 `tools/quality_gate.py`、`tests/**` 断言、`.github/**`、`package*.json` 时：先让 Issue 声明这项改动，并在汇报里单列一行说明为什么非改不可。
- "按钮已存在"回答不了"功能是否可用"：按六维验收面的口径回答（Issue #294）。
- 汇报里只写本会话真跑过的命令与真拿到的结果。
