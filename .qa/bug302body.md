## What's wrong
节点生成失败时（如 1800s 超时），node-body 内错误文案被裁剪：scrollHeight=307 > clientHeight=273（裁 34px），overflow 非滚动容器。用户看不到完整错误原因。

## Evidence（来自 #297 新建的 scenario_ux_rules.sh U1 规则实测）
TEXT_OVERFLOW: smart_i0lrhvq49rzp > node-body: scroll=601x307 client=601x273 text=生成失败 生成任务执行超时（1800s）

## What I expected
错误文案完整可读：允许换行/滚动/或节点高度自适应内容。

## Fix 方向
错误状态下放开 .node-body 内容高度（或错误条 overflow:auto + 最小高度）；勿破坏 .agent-smart-node/.smart-group-member-node 的 height:100% 布局规范（smart-canvas.css:136/145/198）。

## Acceptance
tools/e2e/scenario_ux_rules.sh 的 u1 转绿且全量 E2E 无回退。
