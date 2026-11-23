# 生成中的原图删除与累积回包（#491）

## 已复现的根因

先前普通终态样例的 Store 与真实 renderer/adopt 接缝未复现节点保留；不能据此把票面判为无问题。可复现的路径是：同一任务先公布 A，用户删除 A，任务最后提交累积的 A+B。另一个节点仍独立引用 A，因此 A 的原件合法存在。旧 `complete_media_generation` 又把 A 写回生产节点和同一条生成历史。浏览器收到 canonical canvas 后确实显示复活的 A。

这不是靠刷新或额外删除修复的前端缓存问题。删除必须保持按输出实例的语义，不能把独立副本一并删除。

## Bisect 与适用边界

在专用 `D:/ff-wt/bisect-ui-491` 执行了真实 `git bisect start 1ce602e d77a47f` 和 `git bisect run`，首个 bad 是 `40e15c4cca1867247902584b0591ce6243720c44`。该提交加入候选逐步发布，以及 `partial` Store 写入；其累积终态回包没有尊重已删除的输出实例。

**good 边界的含义有限**：`d77a47f950f9b9aa6256cb6aef773c887e135cc1` 没有 partial Store API，候选内部也不发布结果，不能在任务结束前执行这条交错操作。探针明确记录 `GOOD_NO_EARLY_PUBLICATION`，不冒充在不存在的 API 上跑通了完整场景。旧 API 的 `logs` / `log_id` 名称被只读适配；不兼容及早期脚本错误按 125 留档，未当成 bad。首次启用这条失败序列的提交与普通终态删除的回归不可混为一谈。

原始 bisect 记录、逐版本隔离根和回包见 `D:/ff-qa/489-488/491/GIT-BISECT-{START,RUN}.log`、`GIT-BISECT-LOG.txt` 和 `bisect-runs/`。

## 修复约束

- `firefly_projects.py::complete_media_generation` 在校验原件及合并前，依据同一生成历史内、带有效输出身份与删除 ID 的 tombstone 排除已删除回包；同时过滤任务回执里的 outputs。
- 图片 worker 在候选第一次 partial 公布前生成稳定 `output_id`，history、最终回执和节点保留该身份。合并按明确的实例身份去重，同任务的新候选即使字节相同，也不是被删实例的重放。仅旧式无 ID 回调保留同一历史内的保守资产匹配，不成为项目级按资产删除的回退。另一条历史或独立副本仍可使用同一原件。
- 任务的 `image_items`、`images` 和 `history_record` 必须来自 Store 已提交的 canonical history，而不是原始累积候选；按 `output_id` 补齐尺寸，不能把同资产的另一实例绑错。
- cascade 的任务轮询已经应用服务端终态时，不再把旧回包写一次；当前节点即使为空也有权威性。以当前节点而非旧对象取输出；旧式完成也不能覆盖被替换的节点。共享轮询门实测将核心引用总数/自由侧收紧为 148/49，没有增加外部依赖。
- partial、最终累积回包和服务重载后都不能复活 A；即使 A 已进入回收区，也不能先验证 A 原件而误报新任务写入失败。
- `firefly_projects_deletion.py::restore_deleted_original` 恢复被删实例时保留后来已完成的任务状态，不重新挂出旧 pending；独立副本保持不变。

图片 worker 的输出身份发布与回执过滤会改变 `run_project_image_task` 的函数体，因此同步
#471 白名单中 `app/routers/api_project_library.py` 这一个成员的实际 AST 指纹；成员、序号、分类及历史上限均不变。

## 验证与证据

提交内回归为 `tests/test_generation_output_delete_491.py`、`tests/test_generation_output_worker_491.py` 和 `tests/canvas_module_units/test-canvas-generation-tasks.mjs`：覆盖累积 final、重复 partial、原件已入回收区、全部输出被删、同任务同字节的新实例、另一历史复用同原件，以及冷读取和媒体仍保留时的撤销。独立副本使用不同的合法 output_id。worker 测试实际执行归档与 Store，只替换 provider 为受控内存 PNG；cascade 测试加载真实轮询与模块，覆盖 canonical 空结果、旧对象替换、旧式回包和真错误。

边界：删除使原件进入回收区、随后同字节生成重新导入，再撤销旧删除时，现有恢复通道仍可能报「项目媒体已恢复」。这条额外恢复路径不在上述撤销通过声明之内；未擅自改成按资产恢复其他实例。

31xx 原生浏览器：3179 基线先真实点击历史里的删除，生产节点当时清空、独立副本保留；随后受控调用**真实 Store** 最终 A+B，节点和历史复活 A+B。3180 修复版同序列只保留 B，刷新后仍为 B，独立副本 A 留存。图片是在私有根生成的内存 PNG；真实 REST、任务查询、Store、浏览器交互均执行，没有调用模型或生图 provider。

总证据位于 `D:/ff-qa/489-488/491-native/`：`PARENT-*` 请求与状态、`screenshots.json`、各 run 的 `requests.jsonl` / `generation.jsonl` / cleanup。基线与修复截图尺寸不同，不作为逐像素对比。初期截图构图、脚本语法等失败及隔离边界的非关键 403 仍保留；不报无警告。浏览器截图已由主会话实际读取核对；两服务及自有项目已结束，原件证据保留。
