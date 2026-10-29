# Wave 2 派发台账（2026-10-28 下午，总控）

基线：wave1-live @ 07d08ca（含 238/263-1/235/234/240/246*/249；*246 见下）。

| 票 | 任务 | worktree/分支 | 状态 | 备注 |
|---|---|---|---|---|
| #266 | MCP 工具面扩面（从 #249 retitle 拆分建票） | wt-266 issue/266 | 派发 #1（55m） | 17 工具地图+handler 分发链注入 |
| #251 | undo 栈持久化 ops.db（ADR 0021 租户之二） | wt-251 issue/251 | 派发 #1（50m） | 25m 内必须落第一 commit |
| #252 | IME 回车 + Markdown 围栏 | wt-252 issue/252 | 派发 #1（45m） | 全仓 Enter 点位清单化 |
| #254 | 逐图生成——数据层切片 | wt-254 issue/254 | 派发（38m 硬预算） | 前两轮 55m 派发均 0 commit；本轮纯函数+单测-only |
| #262 | 任务中心（含 #258 usage 租户最小前置） | wt-262 issue/262 | 派发 #1（55m） | #258 不关票，剩余差异清单进 result |

失踪审计：#254 两轮 worker（95 分钟）无 commit——canvas-generation-tasks.js 395KB 通读耗尽预算；教训：大文件票必须切片+限时地图。
#246 从未开工（两轮记录空），仍 OPEN，排队重派。
#249 已按卫生包实质关票（范围纠偏注释补挂），工具面范围由 #266 承接。

## batch-3 派发 + 全面对账（19:4x）

真相修正：会话B并行管线已将 #244/#249/#254(切片)/#252/#261/#260(切片) 合入 wave1-live
（git merge-base --is-ancestor 实测全部 IN）。我的 wave1-next 系冗余，已删；
其 fast 14.9s + domain 122.7s 全绿结果对 wave1-live 现内容依然有效。

- push：origin/wave1-live = e879b6b（+49 提交首次推送完成）
- 关票：#252(1e41bae)、#261(0924f41)、#265(92ceda8 已在基线)、#254(fd2357f 切片1+数据层)
- #260 口径修正：部分交付（聊天附件扩视音频 TODO）→ 拆出 #268
- 新派发（基线 e879b6b）：#256 OCR→prompt 节点（del_mvs00djy_6tvf）、
  #257 版本树 ops.db（del_mvs00djy_slvl）
- 排队：#258（usage 租户，待 257 ops.db 迁移合入后串行）、#255 分层大票（3h 时箱，错峰）、#253
- 会话B在途：#262 任务中心、#263 234证据补挂、#266 cache_backend、#267 fal
- 分工纪律重申：A 不碰 agent/fal/generation/media；B 不碰集成线 ff。
- #256 收口：issue/256（4 提交，禁区零触碰）合入 int-256（基线 b21aae1）；
  总控干净 worktree 门 fast 15.9s ✅ domain 139.5s ✅（含浏览器步——worker 环境报"无法启动浏览器"
  为并发 chrome 争用，非代码问题）。待主区 html 脏文件清后 wave1-live 吸收 int-256。
- #257 收口：issue/257（6 提交，ops.db versions_* 租户 additive 迁移，worker 自证 fast+domain 绿+UI冒烟截图）合入 int-256；总控集成 fast 15.2s + domain（见下）全绿。
- #255 收口：issue/255（6 提交，分数坐标骨架+MIT 移植 layers/vectorPath+LayersPanel；NOTICE.md 双登记冲突已手工合并）合入 int-256；总控 fast 16.1s + domain 132.7s 全绿。票面纠偏：vectorPath.ts 实为 54 行；utils.ts:250-268 于参考版本不存在（even-odd 自研）。遗留：图层全链浏览器旅程测试 → #269。
- #258 收口：issue/258（8 提交，firefly_usage.py MIT 移植 + usage_001 租户 + pi/codex 双采集 + 查询 API；+42 用例）合入 int-256（NOTICE 三段登记手工合并）；总控 fast 15.5s + domain 128.2s 全绿。batch-3 四票（255/256/257/258）全部收口。
- #268 收口：合入 int-256，fast 16.1s + domain 138.1s 全绿；音频转写选型拆 #270（ready-for-human）。
- #253 收口：issue/253（4 提交：snap+对齐线/右键操作卡+Tab 快编/快捷键扩表）合入 int-256；worker 140m 时箱在门调查阶段被杀但工件全推；总控代跑 fast 16.2s，domain 首败（snap 与旅程断言语义冲突）→ 修断言语义后 domain 133.8s 绿。
- #253 集成进 wave1-live（merge 7cec429）：棘轮 246/964/174/811+ADR0020 漂移日志+ui-integrity createMenu 基线刷新；trial 212.3s 全绿。wave1-live 现含 batch-2/3/4 全量十一票。
- #253 收口补记（总控代交）：core_surface 拖拽断言语义修复（存储==落盘实时位置，适配吸附）已随 int-256 合流。
- #269 收口：issue/269（layers_panel_browser 33 断言全链旅程，注册 trial+release+并行组；零产品 bug——#253/#255 交互获裁判背书）。总控 int-256 合入后 fast 16.0s + trial 214.5s 全绿（含新步）。
- #251 收口：undo_snapshots 租户+GET/PUT 路由+前端 1.5s 防抖/恢复（棘轮 247/965/175/812 随分支入 wave1-live）；总控 fast+domain 见上。
- #246 真集成完成：wave1-live merge（干净合入）+ 总控 fast 16.1s + domain 130.8s 全绿；此前误提前关票（门证据未落地即关），已补真证据并纠偏评论。
- #237 收口（总控代交，三波 worker 接力）：worker1 死于110m（留取消响应修复+RED测试）、worker2 死于thinking-only、worker3 完成审计并确认**票面语义为准**（ops.db 租约表系简报误引旧草案，票面=ComfyArt 285fd93 先例：即时终态+interrupted 表达+partial 保留）；总控接管固化 db2e81f，fast 16.1s + domain 130.6s 全绿（/tmp/gate-237-fast|domain），merge 进 wave1-live 后再过 fast（/tmp/gate-237-integration）。
- #236 收口（总控代交，四波 worker 接力）：源码层七提交（CLI 脚本+api_jimeng 退役/WSL 更名 lenient_extract_json/model_specs/services/routers/main）+ 四波收口（poster 再生守卫与 7 例失败测试修复、agent tool secret 声明表按 ADR 0022 生成链对齐、usage 迁移 usage_001→ops_migrations、ADR 0022 登记、棘轮 247/967/174/812 不变）。worker 基于 20b2259（#237 前），no-ff 合入后 fast 16.6s + domain 139.9s 全绿（/tmp/gate-236-integration|domain）。纠偏记录：首轮收口曾误记 ff+tag 未落盘（ref rebase 后幻觉记忆），本次每步 ls-remote 验证。
- 主线切换（维护者批准）：v2026.10.wave1 发布（main=76d24de no-ff 合入 wave1-live@5a37d2a，tag 验证在远端）。此后 worker 基线=origin/main，集成线=main；#247 不立项关闭；#263 取代关闭；#235 RunningHub 验收段维护者裁定作废（provider-agnostic 部分继续）；#248 方向确认=服务器推理+画布纯 API 接入。B 会话使命结束，其四票接管：#235/#262/#266 已派工，#267 待 235 合流后派（generation.py 冲突面）。
- 维护者批复（m02674 时点）：#248 走 P0 假服务器 TDD（真服务器未建）；#259 要——决策评论已签（anno_* 持久对象 + #242 确认门 + 任务中心缓挂）。拆票 #271（provider P0）/#272（批注层 P0）已派工。在途 worker：235/262/266/271/272；排队：267（待 235 合流，generation.py 冲突面）、259-P1 runAll（待 272）。
- #235 收口（接管 worker del_mvt1680a_hcnx）：前 worker 零提交（只留含 RunningHub 用例的草稿，已重写为 provider-agnostic）。交付 7abf30f：IMAGE_TASK_UNKNOWN_STATUSES+连续2次未知→502 显式终态（文案带 task_id+重试指引，不再空转 504）、fetch 404→结构化 NOT_FOUND、视频候选全 404→显式不存在、task_query 未知态不得当 running。新测试 11/11；全量 1551 passed（基线+11）。集成后 fast 16.5s+domain 131.5s（/tmp/gate-235-integration|domain）。#267 解锁派工。
- 双超时阵亡与接管：#262 worker1 死于140m（4脏文件零commit，接管 del_mvt66q90_lexb 120m+范围熔断保命线=列表/成本列/取消）；#266 worker1 死于150m（零commit零残留，侦察结论在其活动日志，接管 del_mvt6jzzh_ghmo 110m+先只读工具 canvas.view/view_media 后 edit_*、batch_edit 允许拆尾票）。教训：时箱 150 贪大，110-120+熔断条款为标准配置。
- #271 worker1 死于140m（零commit零残留，时间耗在通读方案/docs；日志尾=正在写 result 草稿）。接管 del_mvt7x6om_tyzj：95m 箱+锚点直给（main.py:1106/1092、core.py:841-905、活样本 provider-ms5fzxu7）+三级保命线熔断（文本通路假服务器→图像通路→specs/声明表），到点前20分钟强制收尾。三连同款教训：新票默认≤120m+锚点直给+熔断条款。
- #272 收口（总控代交）：三波接力——首波 150m 烧穿但四提交在盘（租户/CRUD/前端层/undo/单测）、接管波 provider 猝死零动作、总控固化棘轮（242/959/170/805→244/961/171/806，批注层首次非核心引用 core 名家族）+ 全绿（去 bun 项 1560 passed）+ fast 16.6s/domain 155.4s；merge 进 wave1-live 后 fast+domain 复核。
- #262 收口（总控核验）：接管波虽死于时箱但产出完整——5 commits（3ac2ff6 usage 按节点聚合 / 34a656c /api/global-tasks 数据面 / 8e5b121 验收8例 / 29b9af3 总控台面板+focus_node reveal / e8b8501 cache-buster 不变量修复）；总控跑实：29 tests passed + 浏览器验收脚本 global_settings_browser.py 三视口全 PASS（含主题对比度/移动端布局断言）+ fast 18.3s/domain 142.9s。merge 进 wave1-live。
- #267 收口（接管波）：registry 红点=缺 fal 家族合同（根因），补齐转绿+queue mock 断言；尾票登记（specs 清单核对/前端能力展示）见 issue-267-result.md；集成 fast 全绿。
- #267 实据收口（接管波）：实交付=model_specs fal-all 登记+queue 断言扩充+generation 小修（issue/267 已 push 合并）；前记录'registry 红点根因'系未验证假设，更正——registry 本绿。教训：worker result.md 的根因陈述必须实测后才准进票面（误关→重开已纠偏）。
- #271 收口（接管波）：三保命线全过（假服务器文本/图像双通路+能力登记+声明表），7 例测试+集成 fast 全绿。两票 worker 均'写完 result.md 但没 push 分支'——收尾 push 必须提前到收尾模式第一步（模板再改）。
- #266 尾票（#273，del_mvtbpocr_3q7i）：canvas.batch_edit 落地——edit_crop 循环+crop 参数回写节点（幂等），三处齐（router/mcp/canvas-tools.ts），5 例验收；edit_* 剩余（erase/matting/outpaint）留 #273 继续。merge 进 wave1-live。
