# 领域文档

本项目采用 single-context 领域文档结构。

## 开始探索代码前

按需读取：

- 根目录的 `CONTEXT.md`
- `docs/adr/` 中与当前工作有关的架构决策

如果文件尚不存在，直接继续工作，不要把缺少文件视为错误。`/domain-modeling`、`/grill-with-docs` 等技能会在真正形成术语或决策时创建它们。

## 文件结构

```text
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
└── static、main.py 等实现文件
```

## 术语规则

Issue 标题、规格、测试名称和代码设计应使用 `CONTEXT.md` 中定义的领域术语，避免随意使用同义词。

如果需要的概念尚未定义，应判断它是临时措辞，还是需要通过 `/domain-modeling` 正式记录的新概念。

## ADR 冲突

如果新的实现或提案与已有 ADR 冲突，必须明确指出冲突并说明重新讨论的理由，不能静默覆盖既有决定。
