# #447：Codex 思考强度切换复现记录

## 结论

2026-11-21，在当前分支未复现「medium 对话后改为 xhigh，下一轮又退回 medium」。没有据此修改产品代码，也不声称修好了一个已复现的回退缺陷；是否关票由 review 决定。

三轮均由真实浏览器操作、真实 Codex CLI 执行。节点持久化值、刷新后的界面和实际启动参数一致保持 xhigh。宿主没有回读有效档位：`effective=null` 是已知信息边界，不等于回退 medium，也不能被写成“xhigh 已确认生效”。

## 环境与隔离

- 代码基线：`7779c68`，本票开始时 HEAD 为 `d39c46d`（#352）。此前的 `1ec5e31` 与 `7779c68` tree 相同，按 review 要求已换到新历史。
- Windows，本机实查 `codex-cli 0.162.0`；模型 `gpt-6.1-sol`。
- Firefly 隔离端口 `3202`；项目名使用 `e2e-tabbit-` 前缀。`FIREFLY_RUNTIME_ROOT`、`FIREFLY_PROJECTS_ROOT`、HOME、USERPROFILE、APPDATA、LOCALAPPDATA、临时目录、Pi/Codex home 均在 `D:/ff-qa/352-447-429/live447/a2/` 下隔离。只读取并复制宿主认证到专用副本，未改用户配置或项目，未访问用户的 3000 实例。
- Tabbit 专用任务 `ff447-effort`、新建页面、1600×1000；所有设置及发送均用真实菜单/输入框，未注入业务状态。

环境差异必须保留：第一次夹具 `a1` 复制的原生默认模型为 `gpt-6.2-sol`，不在本机 Codex 可见目录，能力接口返回 `unsupported_default_model`，界面无法列出思考档位，未进入对话。因此另建 `a2`，只在隔离配置副本中选目录内的 `gpt-6.1-sol`，默认 medium。没有把第一次环境阻断说成票面回退的根因，也未修改用户的默认模型。

## 实际步骤与结果

同一项目、同一 Agent、同一原生会话：

| 步骤 | 浏览器操作及模型回复 | 实际新进程参数 | 回包/保存结果 |
| --- | --- | --- | --- |
| 1 | 菜单选 medium，发送要求只回复 `EFFORT-MEDIUM-OK`；模型准确回复 | PID 96276，首次 `exec`，`-c model_reasoning_effort="medium"` | requested/submitted 为 medium；节点设置 medium |
| 2 | 菜单改 xhigh，发送要求只回复 `EFFORT-XHIGH-OK`；模型准确回复 | PID 117644，`exec ... resume`，`-c model_reasoning_effort="xhigh"` | settings 返回 200、persisted=true；requested/submitted 和节点设置为 xhigh |
| 3 | 刷新同一页面，验证按钮仍为「思考 xhigh」，不再改设置，发送要求只回复 `EFFORT-AFTER-RELOAD-OK`；模型准确回复 | PID 44580，`exec ... resume`，`-c model_reasoning_effort="xhigh"` | requested/submitted、保存的 `agentHostSettings.codex.effort` 均为 xhigh；revision=4 |

三个终态 `agent_end`，没有 `error` 事件。三轮 actual argv 的 reasoning 配置分别且仅为 medium、xhigh、xhigh。最终状态为：

```json
{"requested":"xhigh","submitted":"xhigh","effective":null,"source":"codex:argv; effective-unverified"}
```

流程中的一次自动化失败也保留：`medium-send.js` 在发送后等待 HTTP `/prompt` 回包超时（30000ms），实际界面走 WebSocket，消息已经成功送出。未重发该消息；随后用只读 UI、事件流及实际 argv 确认第一轮完成。原失败 receipt 未删除，不将它说成应用错误或全程脚本零失败。

## 源码与回归边界

`app/services/agent_adapters/codex_adapter.py` 仅在 `SpawnParams.reasoning_effort is not None` 时序列化 `model_reasoning_effort`。`app/services/agent_host.py::_codex_turn` 使用本轮快照构造参数，并把该值记为 submitted；不能从提交参数推导 native effective。

在 `tests/test_agent_runtime_settings_421_424.py` 增加 `test_codex_medium_then_xhigh_stays_xhigh_on_following_resume`：复用既有隔离假原生进程，真实 manager/adapter 连续发送三轮；仅前两轮改设置，第三轮不重新设置。精确断言每次唯一的 reasoning 参数、模型、原生绑定、首次与两次 resume，以及 requested/submitted/effective 状态。该自动化守卫不替代以上真实 CLI/浏览器复现，也不宣称验证了模型内部强度。

## 证据与收尾

证据根：`D:/ff-qa/352-447-429/`。

- `live447/a2/EVIDENCE.json`：三次启动参数、实际 CLI 版本、最终状态和持久化值。
- `live447/a2/{events.json,spawn.jsonl,medium-observed.json,xhigh-observed.json,reload-observed.json}`：完整顺序及真实回复。
- `447/`：浏览器脚本和逐步 receipt，包括原始失败 receipt；`447/xhigh-after-reload.png` 为刷新后截图。
- `live447/{a1,a2}/cleanup.json`：专用项目和认证副本已移除、隔离服务已停止；Tabbit 页面关闭，finish 返回 `finished=true, forcedCleanup=false, issues=[]`。
- 定向/最终全量与 fast 的结果统一见本批交付记录，不在此预写尚未完成的结果。
