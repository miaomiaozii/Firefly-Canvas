# #493 重新取回：已授权实现，整票待验收

实际授权见 `D:/Firefly-Canvas/.local/agent-work/channel/review-batch-ui/MESSAGES.md:3847,3852–3860,3865–3866`（review `01a1ef85` 经用户 `m05149` 明确确认）：无需再次等待设计稿批准，实现与新截图随整批交付。

`canvas-result-retrieval.js` 是 classic-script 适配器；私有队列 DTO 只用于临时界面状态，不产生媒体，不写节点。`configure` 注入当前项目、canonical history、可控 fetch、重绘与 `adoptCanonical(project)`。宿主注入既有 `mergeReloadCanvasNow`，保留 pending 编辑重放、视口、选择与项目守卫；不调用 `loadCanvas`。

- 顶栏小标记「N 张待取回」打开项目列表，按一次生成记录分组，每组独立提供「全部取回（N）」；按当前项目 GET 队列，按 history 分组；服务器列表中的本项目组不依赖客户端 history 窗口，附着节点/历史/Agent 仍严格匹配原 history/task/result-node。每项展示可信 status/can_retrieve/TTL/bytes；未知进度不显示百分比。
- 摘要区分待取回（含处理中）、已保存、无法取回、等待状态确认。批量按钮以剩余可取回数量计数，处理中保留且禁用；POST 仍只选择当前可操作 item_ids。逐项轮询成功/不可取回立即替换处理中状态，不等待整批结束。
- 节点投影要求 canonical history 的 history/task/result-node 全匹配，且节点 pendingTasks 的 task 与 download.history_id 匹配。Agent 任务状态卡要求 task_id 与 result_node_id 对应同一 canonical history；不从任意错误文本派生动作。
- POST 只发送私有队列 item_ids；同组串行、共享 pending 去重，不吞掉不同项的点击。批量只选择当前可操作项；不同 history 不混批。
- 保存项优先严格匹配 output_id；只有原槽没有 output_id 时才兼容 output_order，不能用顺序替代另一输出身份。DTO 不包含可物化资产；既有保存图片保留宿主选择态、媒体签名与操作。
- 项目切换清空并置空轮询 timer；已触发 timer 同样释放句柄。旧项目回包不能阻止新项目 `sync()` 首拉，也不能安装到新项目。
- 每个待取回的生成输出项保留自己的占位图与「重新取回」按钮、剩余有效时间；多项缺失时另有「全部取回（N）」，N 随剩余可取回项更新。批内逐项显示成功、处理中或不可取回，部分失败不覆盖成功项；明确注明「不会重新生成、不扣费」，不与重新生成混放。链接约 24 小时有效是用户向 Soon 确认的依据，不是保证；过期只查询原任务或明确无法取回。
- #495 的结果格与待取回占位格保持 **192×144**，GAP **12**、PADDING **20**，字体不变；这是视觉格尺寸，不是生成输出项的身份定义。
- 入口使用既有顶栏图标按钮流，不另设绝对横坐标或字体；节点摘要复用 `pending-batch-stack` / `batch-statusbar` 的内部保留状态行，网格在固定节点范围内滚动，不向端口外生长。
- 请求失败与 canonical 接收未确认均显示错误；迟到的旧项目回包不改变新项目。
- 新脚本沿用 HTML 现有缓存 stamp；发布缓存刷新由现有 `app/asset_cache.py` / `tools/refresh_cache.py` 和父代理处理，未发明 manifest/version。

私有队列只辅助下载续传与投影，不是权威画布、生成历史或可复建项目的数据源。客户端只选 item_ids，公开 DTO 不泄漏原结果 URL、凭据或内部来源信息；取回不创建生成任务、付费尝试或新 Agent 轮次。

普通退出先关闭并等待正在取回的工作及阻塞写盘，再关闭项目存储；已通过权威归属校验的原结果保留续传身份，不替换历史或当作生成取消。显式用户取消、无检查点或归属失效仍走取消/拒绝路径。Agent 批量把 `download_failed` 视为已结算的取回条件，保留原结果 DTO，并沿既有批量策略继续或停止，不轮询到永久阻塞，也不重新生成已接受项。

## 局部证据（实现核对时点；最终结果以交付清单为准）

独立 Node VM 测试覆盖真实 nodeBodyHtml、history card renderer、bindSmartHistoryWindow 与取回事件。`tests/test_result_retrieval_projection_ui2_493.py` 用真实 ProjectStore/checkpoint、磁盘重启与进程内 ASGI GET 导出 `FIREFLY_UI2_QA/canonical-projection.json`；随后显式运行 `tests/canvas_module_units/integration-canvas-result-retrieval-projection-493.mjs`，验证真实 JSON 到节点/历史/Agent 匹配及实际 canonical 合并保留未保存正文、位置、选择和视口。该集成入口依赖前一步导出的运行数据，不加入只依赖静态源码的 `test-*.mjs` 自动发现。

`mergeReloadCanvasNow` 当前实现所有路径返回 boolean：错项目、拖拽/框选延迟、HTTP/接收失败均 false，成功为 `Boolean(applyMergedServerCanvas(...))`。取回适配器保留该确认约束，不将未确认合并当成功。隔离证据见 `D:/ff-qa/493-impl/UI2-ACTIVE.json`；无浏览器、监听服务器或付费调用。

父代理已有真实产品夹具及原生操作/截图，位置 `D:/ff-qa/493-product-ui/native/`；它们不是仅原型或 VM 证据。此目录的 `NATIVE-FINAL-CAPTURE.json` / `FINAL-IMAGES.json` 仍保留一次真实 495 几何断言失败，不能用文件名「FINAL」推出全绿；`D:/ff-qa/493-product-ui/REPORT.md` 也是先前夹具时点报告，不作为当前实例状态。这里不宣称准确截图总数或完整视觉验收。这份核对记录写入时，最新 retry/auth/lifecycle 集成尚未重建并验证 fresh fixture；最终交付清单负责将最终源清单与 12 场景、双主题、1366 触控/键盘、删除节点只存历史和 guarded canonical 合并逐项对应，后续新证据独立归档，不覆盖旧失败。不得通过放宽 coupling 上限或 VM 字符串代替原生几何验收。
