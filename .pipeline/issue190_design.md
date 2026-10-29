# #190 设计笔记（主 agent，2026-10-18）

工单：`canvas save conflict: 409 后重试循环空转，保存彻底停止，未保存状态跨刷新存活`
https://github.com/Accurscore/Firefly-Canvas/issues/190

## 实测事实（我亲自核实）

- 保存重试循环：`static/js/smart-canvas.js:6741-6752`
  ```js
  async function saveProjectCanvasUntilSettled(projectId, options = {}) {
      const maxAttempts = Number(options.maxAttempts || 4);
      let lastResult = null;
      for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          const result = await saveProjectCanvas(projectId, options);
          lastResult = result;
          if (saved !== true) return false;   // ← 409 时在这里提前返回
      }
      return lastResult;
  }
  ```
  `saved !== true` 时**直接 return**，所以 `maxAttempts:4` 的循环在 409 冲突下**一次都不会重试**。
- 真正有 409 rebase 重试的是另一层：`static/js/project-canvas-mutations.js:344-386`（`maxConflictRetries: 2`）。
- 409 来源：`app/routers/api_project_library.py:104` 与 `:140` 返回 `canvas_conflict`；`static/js/project-canvas-mutations.js:370` 消费 `canvas-conflict`。
- 用户症状：只有一个画布标签页，偶尔发生；保存彻底停止，未保存状态跨刷新存活。

## 已排除的假设

- **不是多标签页并发**（用户只有一个标签页）。
- **不要加超时/上限**：#189 已实测证明加 cap 会取消在途工作、把 bug 换回来。

## 待查（worker 要做）

1. 单标签页下 409 从哪来？候选：#197 的 persister 与用户操作互撞；`baseStateVersion` 陈旧；服务端版本号在别的路径被推进。
2. 保存彻底停止后，为什么没有恢复路径？（`persistPending` 卡住？后续编辑不再触发保存？）
3. 未保存状态跨刷新存活 = 本地有未保存内容、刷新后没重放 —— 与 #197 的 pending-action 持久化是同一族问题。

## 修的方向（最小正确改动，用户明确反对过度复杂化）

让 `saveProjectCanvasUntilSettled` 在 409 时**真的走重试**，而不是提前 return。具体：识别 409/`canvas_conflict` 结果，走 `project-canvas-mutations.js` 已有的 rebase-retry 路径（或复用其 `maxConflictRetries` 语义），非冲突失败才保持原来的提前返回。

**不要**新增自动平移/自动拉回之类的 UI 行为（用户明确禁止）。

## 并发状态

`static/js/smart-canvas.js` 目前被 #197 的 worker 占着（`del_mvdao0vz_us1u`）。#190 必须等它结束再开工，否则会互相踩。
