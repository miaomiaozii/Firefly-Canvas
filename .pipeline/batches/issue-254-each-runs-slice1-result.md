#254 逐图生成 数据层切片1（issue/254）

## 交付
- static/js/canvas-generation-tasks.js:259+ EACH_RUN_RESULT_GAP / expandEachRuns（纯展开器，ctx 注入 nodeRect）/ runEachImageGeneration（编排器：整批一次 pushUndo；每参考图右侧 +140px 建结果节点，refs 只含该图，走 runApiGeneration 单次提交）。

## 边界
- 纯数据层：无 UI 入口按钮（切片 2：批量面板 kind==='each' 透传 + 工具排入口，UI 在 smart-canvas.js）。
- 落点无碰撞扫描（#246 freeSpot 未实施，见 92ceda8 更正记录）——多参考图同排会重叠，切片 2 处理。
- undo 粒度 = 整批一次（与批量生成惯例一致）。

## 验证
- node --check 语法绿。
- 契约测试 tests/test_each_runs_contract.py：源码断言（纯展开器不引用 canvasState/canvasCore/DOM；编排器 pushUndo 恰一次；refs 切片 1；落点 = rect.x+width+GAP）。
- quality_gate fast。

## 下一切片 TODO
1. smart-canvas.js 批量面板/选择态入口：多选参考图节点 → runEachImageGeneration(selectedRefNodes, prompt, runSettings)。
2. 落点行排：等 #246 freeSpot 落地后接上（避免结果节点互相重叠）。
3. 浏览器证据：多选→each→N 结果节点各归各右侧（走 trial）。
