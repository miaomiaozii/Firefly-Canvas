---
status: accepted
---

# 以增量传输和 JSON 快照保存项目

核心画布以约 500 个节点和 1000 条连线为现实容量目标，不引入 SQLite。前端只提交发生变化的节点、连线或设置，后端合并变更并在后台节流写入完整 JSON；媒体与生成历史使用独立文件和目录，避免画布状态膨胀。该方案必须先通过性能原型，验证后才能接受。

## Project layout

```text
<project>/
├── project.json
├── canvas.json
├── canvas.json.bak
├── history.json
├── media/
├── previews/
└── trash/
```

## Consequences

- 拖动过程中不写磁盘，鼠标释放后只提交最终节点位置；其他高频编辑合并后提交。
- 后端最多每 1–2 秒在后台保存一次，将新状态写入临时文件，成功后保留单份 `.bak` 并原子替换正式文件。
- 正常撤销只存在于当前会话；正式文件损坏时允许用户从单份 `.bak` 恢复。
- 节点只保存项目媒体资源 ID，禁止保存媒体二进制、base64 或重复缩略图。
- 性能原型使用 500 个节点、1000 条连线和接近上限的生成历史进行验证。

## Acceptance criteria

- 500 个节点、1000 条连线的项目在 3 秒内打开。
- 连续拖动节点时不出现超过 100ms 的明显冻结。
- 松开节点后 2 秒内显示保存完成，保存期间画布仍可操作。
- 强制关闭并重新打开后，最后一次已显示保存完成的修改必须存在。
- 正式文件损坏时能够从单份 `canvas.json.bak` 恢复。

## Validation

2026-07-21 在 Windows 10.0.26200、AMD64 Family 25 Model 97、63.64 GiB
内存、D: 盘、CPython 3.10.11 和 Headless Chrome 150.0.7871.129 上完成
Issue #2 性能原型。原型源码与可复现命令保存在独立的
[`prototype/issue-2-incremental-json-save`](https://github.com/miaomiaozii/Firefly-Canvas/tree/08fa3a315156c6e6f5f2420bcb72d27b7f1a063e/prototypes/adr0010-incremental-json-save)
分支提交中，不并入正式产品代码。

原型加载 500 个 DOM 节点、1000 条 SVG 连线和 4900 条生成历史。一次冷浏览器
启动的打开与渲染耗时 539.40ms；后续五次为 91.30–108.10ms。连续拖动的最坏
帧间隔为 18.40ms，鼠标松开只发送 39-byte 最终位置变更。使用 1 秒后台节流时，
最坏保存确认耗时 1052.80ms，保存期间最坏帧间隔为 18.20ms。强制退出后已确认
坐标能够重载，破坏正式文件后也能从唯一的 `canvas.json.bak` 恢复。全部验收标准
连续通过，Standards 与 Spec 双轴审查均为 0 findings，因此接受本 ADR。

该结论接受的是存储架构，不是原型实现。正式实现仍须按产品代码标准重写，并在
集成核心画布、真实媒体解码和项目工作流后继续运行行为测试与性能回归。
