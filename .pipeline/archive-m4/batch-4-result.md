# batch-4 结果：#140 节点级模型覆盖 + #141 自治级别

## 改动清单

### 后端

1. **`app/services/agent_host.py`**：
   - 新增 `AUTONOMY_SYSTEM_PROMPT_SUFFIXES` 字典（cautious/standard/autonomous 三档的系统提示词后缀）
   - 新增 `SAFETY_FLOOR_TOOLS` 常量（空 frozenset，预留安全底线工具集合）
   - `AgentSession` dataclass 新增 `autonomy` 字段（默认 "cautious"）
   - `get_or_create_session` 新增 `autonomy` 参数
   - `spawn_session` 新增 `autonomy` 参数，spawn 时拼接自治级别系统提示词后缀
   - 新增 `set_model` 方法：发送 pi RPC `set_model` 命令切换运行中会话的模型

2. **`app/routers/api_agent_nodes.py`**：
   - 新增 `_find_node` 和 `_update_node_field` 辅助函数
   - `create_agent_node` 端点：新增 `autonomy` Body 参数；若未提供则从节点属性读取 model/autonomy
   - 新增 `POST /{node_id}/model` 端点：设置节点模型（持久化到节点属性 + 运行中会话 RPC 切换）
   - 新增 `POST /{node_id}/autonomy` 端点：设置节点自治级别（持久化到节点属性 + 更新会话）
   - `get_agent_status` 端点：返回 `autonomy` 字段

3. **`app/routers/api_agent_tools.py`**：
   - `request_confirmation` 端点：导入 `SAFETY_FLOOR_TOOLS`；autonomous 模式下对非安全底线工具自动批准（直接返回 `{"approved": true}`）

### 前端

4. **`static/js/smart-canvas.js`**：
   - `agentTuiState` 新增 `autonomy` 字段（默认 "cautious"）
   - 新增 `getChatModels` 函数：返回所有聊天模型列表
   - `agentNodeBodyHtml`：header 区新增模型下拉（`data-agent-model`）和自治级别下拉（`data-agent-autonomy`）
   - 新增 `setAgentModel` 和 `setAgentAutonomy` 函数：调用后端 API 设置模型/自治级别
   - 新增模型/自治级别下拉的 change 事件绑定

5. **`static/css/smart-canvas.css`**：
   - 新增 `.agent-tui-settings`、`.agent-tui-model-select`、`.agent-tui-autonomy-select` 样式

### 测试

6. **`tests/test_agent_node_settings.py`**（新增）：
   - `TestAgentModelOverride`：模型覆盖测试（session 创建、spawn、set_model RPC、/model 端点、节点属性读取）
   - `TestAgentAutonomyLevel`：自治级别测试（session 创建、/autonomy 端点、三档确认门行为、节点属性读取）
   - `TestDefaultBehaviorRegression`：默认行为回归测试（无 model/autonomy 属性的节点行为不变）
   - 共 19 个测试，全部通过

7. **`tests/agent_node_settings_browser.py`**（新增）：浏览器测试入口
8. **`tests/agent_node_settings_browser_client.js`**（新增）：浏览器测试客户端脚本
   - 测试：TUI 渲染、模型/自治级别下拉、autonomous 模式无确认对话框
   - 11 个断言，全部通过

## 测试命令与结果

### 单测
```bash
python -m pytest tests/test_agent_node_settings.py -q
# 19 passed, 3 warnings in 2.50s
```

### Agent 相关全部单测
```bash
python -m pytest tests/test_agent_tui.py tests/test_agent_canvas_tools.py tests/test_agent_generate_tools.py tests/test_agent_media_tools.py tests/test_agent_viewport_tools.py tests/test_agent_node_settings.py -q
# 100 passed, 4 warnings in 24.22s
```

### 浏览器测试
```bash
python tests/agent_node_settings_browser.py
# (1424, 900) PASS: {'pass': True, 'assertions': [...11 passed...]}
```

## set_model RPC 确切参数

从 pi RPC 文档（`C:\Users\Administrator\pi-stable-video\packages\coding-agent\docs\rpc.md`）确认：

```json
{"type": "set_model", "provider": "anthropic", "modelId": "claude-sonnet-4-20250514"}
```

参数：
- `type`: "set_model"（必填）
- `provider`: 提供商名称（可选，如果 modelId 包含 provider 前缀）
- `modelId`: 模型 ID（必填）

实现：`agent_host.py` 的 `set_model` 方法根据 model 是否包含 "/" 来决定是否拆分 provider。

## 系统提示词即时生效能力结论

- **确认门范围**：即时生效。`request_confirmation` 端点每次调用都从 session 读取当前的 `autonomy` 属性，级别变更后下一轮工具调用即生效。
- **系统提示词后缀**：下次 spawn 生效。系统提示词在 `spawn_session` 时拼接并作为 `--system-prompt` 参数传给 pi 进程，运行中无法动态更新（pi RPC 没有 `set_system_prompt` 命令）。级别变更后，确认门范围即时生效，但系统提示词中的行为指令（如"多步任务直接执行"）要等下次 spawn 才生效。

## 遗留问题

1. 模型下拉选项在浏览器测试环境中只有"默认"（因为没有配置 API providers），生产环境会显示所有可用的聊天模型。
2. `SAFETY_FLOOR_TOOLS` 目前是空集合（canvas-tools 扩展没有项目级删除工具）。如果将来添加项目级删除工具，需要将其添加到 `SAFETY_FLOOR_TOOLS` 中。
3. 前端模型下拉的选项加载依赖 `apiProviders` 全局变量，如果 providers 还没加载完就渲染 TUI，模型下拉可能只有"默认"选项。这是一个潜在的竞态条件，但不影响功能（用户可以稍后重新选择）。
