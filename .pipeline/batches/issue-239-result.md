# issue-239 结果报告：agent_host.py 超时必杀 + 退出树杀

## 改动

| 文件 | 内容 |
|---|---|
| app/services/agent_host.py | +427/-105 行：run_command wait_for 超时 finally kill+wait；退出路径 returncode None → kill_process_tree（Windows taskkill /T /F）；stop_idle_watchdog RPC 空闲超时（ERROR_KIND_TIMEOUT_IDLE → _kill_process_tree）；cleanup_all 停 watchdog+全会话；kill 记录 reason |
| main.py | +16 行：shutdown 时 cleanup_all 退出树杀接线 |
| tests/test_agent_host_kill.py | 新建：假 CLI 挂死必杀无残留、kill_process_tree 参数、idle watchdog 杀（TestAgentProcess.wait 卡死模拟）、env 恢复保护（.env_backup，防跨用例environ泄漏） |
| tests/stubs/fake_pi.py, fake_pi_hang.cmd | 挂死假 pi 桩 |

## 测试证据（总控复跑）

- 定向：tests/test_agent_host_kill.py + tests/test_agent_tui.py → 53 passed
- 全量：python -m pytest tests/ -x -q → **1313 passed, 1 skipped, 618 subtests（10m15s）**
- 票面门：python tools/quality_gate.py domain → **verdict: passed** (exit 0, 128s)

## 验收对照

| 验收项 | 结果 |
|---|---|
| 假 CLI 挂死 → 必杀无残留单测 | PASS（test_idle_watchdog_kills_hung_session 等） |
| quality_gate domain | PASS（总控在 wt 内实跑） |

## 遗留风险

- 测试中 environ 变异通过 .env_backup 手工还原——未来若加全局 conftest 可收编。
- 收尾由总控接管（worker 三轮均超时；实现与测试为 worker 产出，commit/门/报告为总控）。
