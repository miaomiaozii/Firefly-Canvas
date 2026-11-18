## Agent skills

### Issue tracker

本项目使用 GitHub Issues 管理需求、规格和开发任务。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分类流程使用五种规范角色，并映射为带中文说明的 GitHub 标签。详见 `docs/agents/triage-labels.md`。

### Code comments

注释只写**决策**：为什么这样写、必须守住的约束、踩过的坑。需要追溯来源时附票号或 ADR 号。改动过程（「第 N 轮」「此前……」）写进提交说明。

改到哪个函数，就顺手清理该函数里的三类注释：复述代码的、记录改动过程的、已与代码不一致的。决策注释保留；判断不准时保留，并在交付里列出。

### Domain docs

本项目采用 single-context 领域文档结构：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。详见 `docs/agents/domain.md`。
