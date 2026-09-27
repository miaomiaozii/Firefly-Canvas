# batch-4 对抗审查报告：#140 节点级模型覆盖 + #141 自治级别

审查方式：主会话逐条亲验（不信 twin 的 result.md 声明），发现问题直接修复。

## 验收逐条核对

### #140 节点级模型覆盖
| 验收项 | 结果 | 验证方式 |
|---|---|---|
| TUI header 有模型下拉，列出 /api/models 所有聊天模型 | ✅ | 浏览器测试：model select rendered + has options |
| 选择后当前会话立即切换（pi RPC set_model） | ✅ | 单测 TestAgentModelOverride（set_model RPC 参数）+ 代码亲验 set_model 方法 |
| 节点属性持久化（刷新后保留） | ✅ **修复后** | 浏览器测试两阶段 reload：`autonomy persisted after refresh: got autonomous` |
| 未设置 = 默认模型，行为与现状一致 | ✅ | 单测 TestDefaultBehaviorRegression + 代码亲验（model 默认 ""） |

**set_model RPC 参数亲验**：对照 pi 文档 `docs/rpc.md:218`，`{"type":"set_model","provider":...,"modelId":...}` 与实现一致 ✓。`set_model` 方法按 model 是否含 "/" 拆分 provider，正确。

### #141 自治级别
| 验收项 | 结果 | 验证方式 |
|---|---|---|
| TUI header 有自治级别下拉（谨慎/标准/自主） | ✅ | 浏览器测试：autonomy select has 3 options |
| 谨慎 = 现状（多步先计划确认） | ✅ | 代码亲验：cautious 后缀为空字符串（= 现状 DEFAULT_SYSTEM_PROMPT） |
| 标准 = 直接执行边做边报 | ✅ | 代码亲验：standard 后缀"多步任务直接执行，边做边报" |
| 自主 = 直接执行，仅安全底线触发确认 | ✅ | 代码亲验：autonomous 后缀 + 确认门 `autonomy=="autonomous" and tool not in SAFETY_FLOOR_TOOLS → auto-approve` |
| 安全底线工具集（项目级删除）始终需确认 | ✅ | 代码亲验：SAFETY_FLOOR_TOOLS 常量（当前空集，canvas-tools 无项目级删除工具，注释说明） |
| 级别变更即时生效（确认门范围） | ✅ | 代码亲验：/autonomy 端点更新 session.autonomy，确认门每次调用读 session.autonomy |
| 节点属性持久化（刷新后保留） | ✅ **修复后** | 浏览器测试两阶段 reload |

## 审查发现 + 修复（2 处）

### 发现 1：后端 `_update_node_field` 缺 revision 冲突重试 + 重复实现 `_find_node`
- `api_agent_tools.py` 已有 `_find_node`（带 `_open_canvas`）和 `_apply_mutations`（8 次重试 revision 冲突）。
- twin 在 `api_agent_nodes.py` 重复实现了 `_find_node`（且 `store.open_project` 调了两次），并新写 `_update_node_field` **无重试**——并发下 revision 冲突会直接 409。
- **修复**：删除重复的 `_find_node`，`_update_node_field` 改为复用 `from app.routers.api_agent_tools import _find_node, _apply_mutations`；`_app_main` 解析移到文件顶部（与兄弟文件一致），删除文件尾重复段。

### 发现 2：前端 TUI 下拉框不从节点持久化属性初始化（刷新后不显示已存值）
- `getAgentTuiState` 只设默认值（model='' / autonomy='cautious'），`state.model`/`state.autonomy` 仅在用户改下拉时设置，**从未从 `node.model`/`node.autonomy` 初始化**。
- 后果：后端持久化了，但刷新后下拉框显示默认值而非已存值（UI 与数据不一致）。twin 的浏览器测试没覆盖刷新场景，未捕获此缺口。
- **修复**：
  - `agentNodeBodyHtml`：下拉框 selected 值改用 `node.model`/`node.autonomy`（source of truth，页面加载时从服务端数据填充）。
  - `setAgentModel`/`setAgentAutonomy`：变更时乐观更新本地 `nodes` 数组的节点属性，再渲染。
  - 浏览器测试升级为**两阶段 reload 测试**（localStorage 标记）：首次加载设 autonomy=autonomous → reload → 二次加载验证下拉框显示 autonomous。

## 测试命令与结果（主会话亲跑）
```bash
# 单测（含重构的 api_agent_nodes）
python -m pytest tests/test_agent_node_settings.py tests/test_agent_tui.py tests/test_agent_canvas_tools.py -q
# 72 passed

# 浏览器测试（含两阶段 reload 持久化验证）
python tests/agent_node_settings_browser.py
# PASS: 11 断言（含 autonomy persisted after refresh: got autonomous）

# 全量回归
python -m pytest tests/ -q
# 1024 passed, 351 subtests passed, 0 failed (324s)
```

## 遗留问题（非阻断）
1. **"no confirm dialog in autonomous mode" 浏览器断言是弱验证**：测试环境 pi 子进程 spawn 失败（WinError 2），prompt 未被真正处理，确认框"不出现"是假阳性。确认门 auto-approve 逻辑由单测 TestAgentAutonomyLevel（三档确认门行为）覆盖，浏览器断言仅作 UI 层弱检查。
2. **模型下拉在测试环境只有"默认"**：浏览器测试环境无 API providers，`getChatModels()` 返回空，下拉只有"默认"选项。生产环境会列出所有聊天模型（`getChatModels` 遍历 `chatApiProviders()`）。
3. **系统提示词后缀下次 spawn 生效**（确认门范围即时生效）：pi RPC 无 `set_system_prompt` 命令，系统提示词在 `spawn_session` 时拼接。级别变更后，确认门 auto-approve 即时生效，但系统提示词中的行为指令（"多步任务直接执行"）要等下次 spawn。这是 pi 的能力限制，非 bug。
4. **`SAFETY_FLOOR_TOOLS` 当前为空集**：canvas-tools 扩展暂无项目级删除工具。将来若添加，需将其加入该集合。

## 结论
**PASS（修复后）**。2 处审查发现均已修复并验证（后端重试/去重 + 前端刷新持久化）。单测 72 + 浏览器 11 + 全量回归 1024+351 全绿。#140 + #141 验收全部满足。
