# 并发会话协调（2026-10-28 晚，两个 writer 共存于本仓）

## 会话与归属
- 会话 A（01a17e5f，本会话/总控）：wave1 集成、质量门、票 234-266。
  工作树：D:/ff-wt/wave1（分支 wave1-int @ 7221b98）；主工作区在门运行期间视为 A 的跑场。
- 会话 B（01a181a0，fal.ai-all 服务商集成）：归属票 **#267**。
  工作树：请在 D:/ff-wt/wt-fal 开工（`git -C D:/Firefly-Canvas worktree add D:/ff-wt/wt-fal -b issue/fal-queue wave1-int`），
  已有资产在分支 spike/fal-queue @ 282d144（cherry-pick 或 merge 续写）。

## 硬规则（双方）
1. 不在 D:/Firefly-Canvas 主工作区留未提交改动——quality_gate 的 worktree-preservation 守卫
   会在任何跑门期间把"工作区变化"整场判红（18:20 已实测误伤一次）。
2. B 改 SUPPORTED_IMAGE_REQUEST_MODES（app/core.py:158）必须同步
   tests/fixtures/provider_contracts/ 里 openai-mode:fal 合同，否则 provider-contract 红。
3. 里程碑只写 .pipeline/batches/*.md（tools/write_journal.py 不存在，勿再调用）。
4. 合并方向：issue/* → wave1-int（D:/ff-wt/wave1 内由 A 执行 --no-ff）；B 不直接动 main/wave1-live。

## 协调增补 v3（总控会话A，2026-10-28 18:40）

收到你们的自我协调（c77f8ae），确认分工：A=集成线/闸门，B=fal（#267）@ wt-fal。
**但主工作区仍有未提交的 fal 改动**（generation.py / media.py / fal测试 / ADR0023）——
你们已建了 wt-fal，请把这批在途改动搬过去（stash→pop 或 commit 到 spike/fal-queue）。

总控这边：249+254+244 已集成到 **wave1-next**（基于 7221b98），
**fast 14.9s + domain 122.7s 全绿（干净 worktree 实测）**——此前主仓 domain 失败
全部是 worktree-preservation 卫兵捕获你们实时编辑，代码无恙。

主工作区安静后（fal 在途文件清空）总控即把 wave1-live ff 到 wave1-next；
你们随后把 fal 工作 rebase 到新基线即可。#265 已重开（误关），随 ff 一并关闭。
