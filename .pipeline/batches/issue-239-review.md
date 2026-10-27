# 审核 issue-239-review.md：#239 agent_host 超时必杀 + 退出树杀

**判定：PASS**（总控接管收尾：复跑全量+domain 门，合并 cbac05c，票已关）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| run_command 超时 finally kill+wait | PASS（diff 审查：wait_for 超时 → _kill_process_tree(reason)） |
| 退出路径 returncode None → 树杀 | PASS（Windows taskkill /T /F，参照 tools/quality_gate.py stop_process_tree 约定） |
| 假 CLI 挂死必杀无残留单测 | PASS：tests/test_agent_host_kill.py（含 idle watchdog 用例；stub tests/stubs/fake_pi*.cmd） |
| tools/quality_gate.py domain | PASS：总控在 wt 内实跑 verdict=passed（128s） |
| 全量回归 | PASS：1313 passed, 1 skipped, 618 subtests（总控实跑 10m15s） |

## 过程备注（教训）

- 三轮 worker 均超时（120m 探索 + 75m 实现未 commit + 55m 收尾差最后一步）。**第四轮由总控直接接管**：跑全量、domain 门、写 result、commit 94e2e4a、合并。
- 结论：worker 适合中小票；「大改动+Windows 进程语义」类票要么预注入更细处方，要么总控亲做。
- worker 的 environ 变异用 .env_backup 手工还原——记为 conftest 收编候选。
