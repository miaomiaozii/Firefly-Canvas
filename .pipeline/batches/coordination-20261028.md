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
