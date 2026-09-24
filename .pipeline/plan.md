# M4 实施计划（Agent 节点增强）

## 目标
实施 #117 关闭时拆出的 4 个 M4 issue：
- #140 节点级模型覆盖（US-28）
- #141 自治级别（谨慎/标准/自主三档）
- #142 会话分支（从历史轮次分叉重做）
- #143 并行 Agent（多节点并发执行验证 + 修复）

## twin
- twin: vllm-remote-twin/qwen3.8-27b-twin-ultra-mtp2（thinking :max）
- 派发：subagent worker + model 覆盖 + :max 后缀
- 每批次：twin 实现 + 单测 → 主会话对抗审查（≤2 轮修复）→ 主会话补跑浏览器测试 → 提交（每批次一个 commit，引用对应 issue）

## 批次划分（顺序执行，文件重叠：agent_host.py / api_agent_nodes.py / smart-canvas.js）
- **batch-4**：#140 模型覆盖 + #141 自治级别 —— ✅ 完成（commit 2762c85，审查修复 2 处：后端重试/去重 + 前端刷新持久化）
- **batch-5**：#142 会话分支（fork）—— ✅ 完成（commit 见 git log；twin 实现 + 主会话审查修复 + 13 单测 + 13 浏览器断言 + 全量回归 1030 绿）
- **batch-6**：#143 并行 Agent（并发验证 + 修复）—— ✅ 完成（commit e3ebe00；9 并发测试 + 修复 revision conflict retry bug + 全量回归 1039 绿）

## 节点交互 bug 修复（2026-09-24，QA 彻查后按文件冲突分批）
- **Batch A（#145+#146，commit 3e35430）**：`smartNodeInFlight` 加 agent-task 判定（status queued/running → in-flight，4 处删除入口拦截生成中卡片）；删除带媒体节点后归档延迟 2.5s + undo/redo 取消（`scheduleMediaArchive`/`cancelPendingMediaArchives`），修撤销后图片 404。Playwright 15/15。
- **Batch B（#147+#148，commit 019ce46）**：新增 `isSmartGroupableNode`，agent/conversation/local-edit/agent-task 拒绝进组（入口过滤 + toast `smart.groupUnsupportedType`）；`deleteNode` 对 smart-group 改走 `ungroupNode`（删除=解散，无数据丢失）。Playwright 15/15。
- **#150（commit 9766204）**：回收站预览 z-index 80→95，修缩略图被历史抽屉遮挡。
- 待后续：#149（9 项交互一致性体验优化）、#151（历史抽屉与生成中节点/资源库冲突）、#152（上传面板与历史抽屉重叠）。
- **#144 已修复（commit 9290109）**：`adoptServerCanvas` 整体替换丢本地未保存 mutation → 改用 `rebaseProjectCanvasResponse` 3-way rebase 保留本地工作。Playwright 回归 8/8 + 后端 112 测试通过。issue 已关闭。

## 关键入口（侦察所得）
- 节点数据：`project['canvas']['nodes']` 列表，agent 节点 type=smart-agent，属性直接存节点 dict
- 会话宿主：`app/services/agent_host.py`（416 行）——spawn_session(session, model) 已支持 model 参数
- Agent API：`app/routers/api_agent_nodes.py`——create_agent_node 已接受 model Body 参数
- 确认门：`app/routers/api_agent_tools.py` `/confirm` 端点（服务端，扩展总是调用，服务端决定门范围）
- canvas-tools 扩展：`app/agent_extensions/canvas-tools.ts`（395 行）——canvas.delete_node / media.delete 两个破坏性工具
- TUI：`static/js/smart-canvas.js` agentNodeBodyHtml（~11674 行起）、agentTuiState（~11359）
- 模型列表：`/api/models` → chat_models（agent 用 chat 模型）

## 收尾
全部批次 PASS 后：主会话逐条核对 4 issue 验收 → 全量回归 → 提交 → 合 main 推送 → 关 4 个 issue
