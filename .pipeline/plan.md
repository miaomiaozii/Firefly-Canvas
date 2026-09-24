# Twin Pipeline 计划：#117 Agent 节点 v2（Pi 终端 TUI 形态）

## 状态
- issue: #117（miaomiaozii/Firefly-Canvas）
- 分支: i117（从 main 切出）
- twin: vllm-remote-twin/qwen3.8-27b-twin-ultra-mtp2（thinking :max）
- 审查: 主会话对抗式审查，每批次最多 2 轮修复

## 侦察结论（2026-09-23，主会话）
M1/M2/M3 大部分已实现（前几轮会话完成）：
- 后端: app/services/agent_host.py（pi --mode rpc 子进程管理、--session-dir 项目内持久化、--continue 自动恢复、崩溃 auto-respawn）+ app/routers/api_agent_nodes.py（create/delete/status/prompt/steer/follow_up/abort/confirm/stream WebSocket 全套）
- 扩展: app/agent_extensions/canvas-tools.ts（17 个 canvas.*/generate.*/media.*/viewport.* 工具，delete_node/media.delete 有确认硬门）
- 前端: static/js/smart-canvas.js 11331+（agentTuiState、WebSocket 流、⚙/✅/❌/⏳ 活动行、thinking 默认隐藏、follow_up 排队、状态灯、确认按钮）
- 测试: tests/test_agent_tui.py（fake_agent_host）、test_agent_{canvas,generate,media,viewport}_tools.py、agent_{,ui_,deps_}browser 浏览器测试

对照 28 个用户故事，缺口：
- US-21 旧"先计划后执行"UI 整体移除 → **未完成**（前端创建入口已无，但渲染残留 + 整套后端 plan/execute 机制还在）
- US-27 项目删除/回收站时 agent 会话生命周期 → **未完成**（session 文件在项目目录内随项目走，但项目删除时运行中的 pi 子进程无清理）
- US-22 默认跟随全局默认模型 → 设计满足（app 无全局文本模型设置，pi 自带默认即全局默认；model 参数管道已存在），batch-2 记录确认
- M4（US-28 节点级模型覆盖/分支/并行/自治级别）→ spec 明确 Out of Scope，不做

## 批次
- **batch-1（US-21）**：移除旧"先计划后执行"UI 与后端机制，旧项目 agent-task 节点保留只读 legacy 渲染
- **batch-2（US-27 + US-22）**：项目删除/回收站时清理 agent 会话子进程；确认模型默认行为并记录

## 收尾
全部批次 PASS 后：主会话逐条核对 28 故事 → 全量回归 → 提交（每批次一个 commit，引用 #117）→ 合 main 推送 → 关 issue

## 完成（2026-09-24）
- batch-3（TUI 规格细节：活动行折叠/状态灯+预览/流式 markdown/多行输入）已提交 cf07258
- 28 故事终验发现 2 缺口并补齐（c5f366e）：
  - 故事 18：输入框运行中不再禁用，Enter 自动排队 follow-up
  - 故事 26：💭 按钮切换思考过程（默认隐藏）
- 清理 4 对旧 Agent UI 浏览器死测试（agent_browser / agent_ui_browser / agent_deps_browser / user_session_browser，batch-1 移除旧 UI 后遗留，质量门/CI 不引用）
- 浏览器测试 agent_tui_polish_browser.py 扩至 25 断言全过；全量回归全绿
- i117 分支已合 main 并推送；issue #117 已关闭（M4 按 Out of Scope 不做）
