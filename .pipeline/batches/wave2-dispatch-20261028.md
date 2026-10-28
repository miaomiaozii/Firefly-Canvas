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
