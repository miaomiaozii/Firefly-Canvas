## Parent
#273（能力选型 spike 已交付：.pipeline/batches/plan-273-edit-capabilities.md 决策表 A3 行）

## What to build
canvas.edit_outpaint agent 工具：把站内手工扩图链（前端红底合成）复刻到服务端，agent 一句指令即可扩图。纯 PIL 搬运，不需要新模型能力。

## Acceptance criteria
- [ ] 服务端复刻 applyImageOutpaint 合成语义（锚点 static/js/canvas-image-editor.js:1944-1987 红底合成 + node.outpaintSize）：PIL pad+paste 生成扩图画布，结果作为新图节点内容，提示下一步 generate.trigger 填外区
- [ ] REST core 放 app/routers/api_agent_tools.py；双面一致（ADR 0022）：app/agent_extensions/api_mcp.py TOOLS(list @:305)+TOOL_IMPLS 与 app/agent_extensions/canvas-tools.ts registerTool 同步新增 canvas.edit_outpaint
- [ ] 写操作入 CONFIRMATION_GATED_TOOLS（api_mcp.py:90，现 5 项）
- [ ] tests/test_mcp_endpoint.py:183 工具面守卫计数 22→23 同步
- [ ] TDD stub 测试（mock 生成/资产写入）；错误文案带下一步建议
- [ ] quality_gate.sh fast + domain 绿；node --test 两个工具面 mjs 全绿；全量 pytest 对照 origin/main 零新增回归

## Constraints
- 不新增依赖；不走 fal.ai/ComfyArt；遵守 ADR 0007（不向模型发蒙版）
- 只建分支提交，不 push 不合并（总控在主仓合并）

## Blocked by
None — 立即开工。
