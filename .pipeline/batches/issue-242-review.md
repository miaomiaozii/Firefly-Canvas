# 审核 issue-242-review.md：#242（ADR 0022）+ #245（ADR 0006 补行）

**判定：PASS**（总控直查 diff + 内容抽查，已合并 2221329，两票已关闭）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| #242 新 ADR 编号 0022、格式照 0019/0020 | PASS（docs/adr/0022-mcp-streamable-http-canvas-endpoint.md，73 行） |
| 证据 file:line 属实 | PASS（worker 全量核对并纠正票面两处偏差：request_confirmation 实际 :510-511 非 :541-579；canvas-tools.ts 在 app/agent_extensions/ 非 packages/） |
| 交叉引用无死链 | PASS（0012/0016/0019 存在；9 处代码引用在行宽内；未引用 untracked 的 delta 文档，防 #261 门禁） |
| status 签署依据 | PASS（accepted，依据 #233 拷问决议 Q3/Q12-a；如维护者想先 proposed 可单行改 frontmatter） |
| #245 ADR 0006 补一行 | PASS（diff +2 行，票面原句独立成行） |

## 过程备注

- 首派 45m 超时（探索耗尽预算，无 commit）；resume `del_mvr05qi2_au0n` 带停探索指令后完成。
- 两处票面事实偏差（行号、文件位置）由 worker 按实况纠正——ADR 比票更准。
- ADR 已把 #243 需要的「已知缺口」清单（假 canvas.get_selection、VIEWPORT_STATE 空转、_apply_mutations 不广播、wait timeout GET body 被忽略）写入，实现票可直接消费。
