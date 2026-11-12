# Agent TUI 彻查 — 工作现场（2026-11-13, 会话 A，因 shell 阻塞中断）

## 任务（用户 m00019 原话摘要）
Agent TUI「太多 bug、很难用」，彻查该区域，从用户角度出发，必须实际用（tabbit）复现，不许只看代码。报告的问题：
1. UI 边界不跟随自动拉伸（截图：节点框远大于卡片内容，右侧大片空白）
2. 输入框太小、不随文字变大
3. 输入的文字要取消节点选择才会显示
4. 图标丑/简陋/与画布风格不统一；建议抄成熟 web TUI 方案
另要求：自查、不要只测代码不测可用性。

## 历史对照
- #301「Feat(UI)：Agent 卡片按 UI 规范重设计」已于 Nov 10 关闭；其验收截图 `.qa/shots/agent301_card.jpg` 与用户当前截图几乎一致 → 上次交付未达标就关票。本次拆票需引用 #301。
- 当前 open issues 只有 4 个（matting/edit 相关），无重复票。

## 已验证事实（代码直读）
- 组件：Agent 节点内 Agent TUI（领域词条见 CONTEXT.md），主文件 `static/js/canvas-agent-tui.js`，样式 `static/css/smart-canvas.css:3708-4340`。节点是画布 DOM 节点（`[data-id]` + `.node-body`）。
- Bug2 成立：`static/js/canvas-agent-tui.js:923` `<textarea rows="1">`；全文件无 auto-grow（仅 `messagesEl.scrollTop` at :1224）。【审计修正，已直读验证】`.agent-tui-input` 有两条规则互相覆盖：`static/css/smart-canvas.css:4095`（透明无边框 padding 2px 0）与 `static/css/smart-canvas.css:4317-4330`（级联在后、实际生效：`resize:none; max-height:80px; overflow-y:auto; padding:6px 10px; 带边框`）。真实生效约束=80px 上限+禁手动 resize；两条同名规则共存本身就是隐患。拆票②必须同时去重两条规则 + auto-grow 突破/替换 max-height:80px 语义，否则 grow 到 80px 卡死。
- Bug4 成立：按钮为裸 emoji 📎💭↪⏳⏹（`static/js/canvas-agent-tui.js:927-932`），28x28 `.agent-tui-btn`（css:4110+），与画布风格脱节；#301 后仍是这样。
- 节点尺寸：`static/js/smart-canvas.js:1615-1629` imageLayout 的 agent 分支用 node.w/h（默认 382x360）；min 330x270（`static/js/smart-canvas.js:14705-14707`）。截图症状=框大卡小，机制未定位（待查）。
- 【审计+自查补充】CSS 层面已排除简单原因：`static/css/smart-canvas.css:195-198` 已令 `.image-node.agent-smart-node .node-body` 100%x100% stretch、`.agent-tui-card` height:100%。另发现死样式层：`.agent-node-card/.agent-node-heading`（css:199-200）在 JS 中零引用（grep 无匹配）——Agent 卡片存在两代样式共存，与 `.agent-tui-input` 双规则同症状。拆票①复现必须区分：(a) 深色卡片内部空白 vs (b) 卡片与节点外框之间空白；并记录该节点持久化的 node.w/h 实际值（怀疑旧持久化 w/h 与内容不匹配）。
- Bug3 方向：`renderAgentNode`（`static/js/canvas-agent-tui.js:1199-1233`）任何 WS 事件都 innerHTML 重建 body（19 处调用点：:72,81,108,338,381,444,460-576,639,1005,1019,1056,1187；grep draft=0 无草稿保留）→ 解释「草稿被吞」；但不解释「取消选择才显示」。复现时须定位选择/取消选择路径上的具体触发链（怀疑与选中态重渲染或事件 stopPropagation 链有关），触发链写进③票正文。
- 输入绑定：`static/js/canvas-agent-tui.js:1024-1060`（Enter 发送、IME #252 处理在 :1030）。

## 拆票计划（未提交）
父票 + 子票方向：①框/卡尺寸不同步 ②textarea auto-grow（含去重双规则、突破 max-height:80px）③输入内容被重渲染吞（草稿保留 + 选择态触发链）④图标/视觉统一（含抄成熟 web TUI 样式参考；顺带清理死样式层 .agent-node-card）⑤研究员补充的其他用户可见缺陷。正文模板见 qa skill（What happened/expected/steps/blocked by），落 `.qa/issues/`，提交用 `gh issue create --repo miaomiaozii/Firefly-Canvas`（见 docs/agents/issue-tracker.md；gh 不可用时不得改用其他存储，须报告维护者）。gh 是否已安装未确认。复现截图存 `.qa/shots/` 并逐票引用（#301 教训：无实拍不关票）。

## 环境故障史（重要）
- 本会话 bash 工具坏：报 "No bash shell found"，只搜 C:\ 默认路径；settings 里 `shellPath=F:\Program Files\Git\bin\bash.exe` 实际存在但工具没吃到（会话创建早于配置生效？）。
- 已改 `C:\Users\Administrator\.pi\agent\settings.json`：defaultTools += `"-bash","+powershell"`（pwsh 7 在 `C:\Program Files\PowerShell\7\pwsh.exe`，已确认存在）。
- 用户重启 Percho 后，本会话（旧会话）工具集不变：bash 依旧坏、无 powershell；acp_delegate 也坏了（子进程秒退 exit 0 无输出，Electron cache 报错，无 activity 文件），第一个研究员 del_mwdo8hsn_otkf 被重启杀死，报告未写出。
- 【监理会话B独立确认】新会话 bash 同样只搜 C:\ 两路径，shellPath 完全未被读取 → 该版本工具链不吃 shellPath，放弃 bash 路线；powershell 工具 + api.github.com（webfetch 可达，仓库公开可读）是可靠通道。

## 新会话接续清单
1. 先跑环境探针：确认 powershell 工具可用；`Get-Command gh,git,pwsh`；gh 缺则装（winget install --id GitHub.cli）。
2. 起 Firefly 应用（run.bat / main.py，端口看 server.stdout.log），tabbit 实操复现 4 症状 + 截图存 `.qa/shots/`。
3. 重新派研究员做根因调查（写 `.qa/agent-tui-findings.md`；上文「已验证事实」可作起点）。
4. 按拆票计划写 `.qa/issues/` 并 gh 提交，标签用 docs/agents/triage-labels.md 映射。
5. 全程在频道 `is-impl` post 阶段进展（双边阶段门，审计会话在另一侧）。

## 路径修正记录（2026-11-12 环境恢复后，shell 可用，直读复验）
- 此前记录里的 `public/...` 路径全部错误（环境故障期工具误报）。真实路径：`static/js/canvas-agent-tui.js`、`static/css/smart-canvas.css`、`static/js/smart-canvas.js`、页面 `static/smart-canvas.html`。行号全部不变（4095/4317/199-200/3708/923/927-931/1199 均已在正确路径下复验）。
- 后端 = FastAPI（main.py + app/routers/api_*.py，含 api_agent_nodes.py / api_agent_tools.py），不是 Node/Express；不存在 server.js/routes/canvas.js/package.json。端口 3000（PID 67912）。
- 画布页 URL：`/static/smart-canvas.html?id=<canvasId>&project=<projectId>&project_mode=1`（static/js/project-list.js:43）。诊断日志：data/canvas-diagnostics.log（POST /api/canvas-diagnostics）。
- imageLayout agent 分支确认在 static/js/smart-canvas.js:1615-1629（默认 382x360，注释明言「尺寸由节点自身 w/h 决定」）。
