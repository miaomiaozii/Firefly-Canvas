# Issue tracker：GitHub

本项目的需求、规格、任务和缺陷统一存放在
`miaomiaozii/Firefly-Canvas` 的 GitHub Issues 中。

所有操作优先使用 `gh` CLI，并显式传入 `--repo miaomiaozii/Firefly-Canvas`。本仓库同时配置了作者的只读 `upstream`，禁止依赖 `gh` 自动猜测目标仓库。

## 前置条件

运行 Issue 相关技能前，确认：

- 已安装 `gh`
- 已执行 `gh auth login`
- `gh auth status` 显示当前账号可以访问本仓库

如果 `gh` 不可用或未登录，应停止写入并明确提示维护者，不要悄悄改用其他任务存储方式。

## 常用操作

- 创建：`gh issue create --repo miaomiaozii/Firefly-Canvas --title "..." --body "..."`
- 查看：`gh issue view <number> --repo miaomiaozii/Firefly-Canvas --comments`
- 列表：`gh issue list --repo miaomiaozii/Firefly-Canvas --state open --json number,title,body,labels,comments`
- 评论：`gh issue comment <number> --repo miaomiaozii/Firefly-Canvas --body "..."`
- 添加标签：`gh issue edit <number> --repo miaomiaozii/Firefly-Canvas --add-label "..."`
- 移除标签：`gh issue edit <number> --repo miaomiaozii/Firefly-Canvas --remove-label "..."`
- 关闭：`gh issue close <number> --repo miaomiaozii/Firefly-Canvas --comment "..."`

多行正文使用可靠的多行输入方式，不要把复杂 Markdown 拼接成难以维护的单行命令。

## Pull Request 作为需求入口

PRs as a request surface: no.

外部 Pull Request 默认不进入需求分类流程。如果以后需要，可以将此设置改为 `yes`。

## 技能约定

- “发布到 issue tracker”表示创建 GitHub Issue。
- “读取相关 ticket”表示执行 `gh issue view <number> --comments`。
- Issue 与 Pull Request 共用编号；无法确定类型时，先检查 PR，再检查 Issue。

## 验收留证（#360）

- UI 类 issue 的验收必须附 tabbit 实测截图：真鼠标/真键盘事件操作（`page.mouse.*` / `page.keyboard.*`），`page.evaluate` 只用于读断言，不用来 `el.click()` 或塞值。截图落 `.qa/shots/`（工树产物，不入库），票面写清路径与场景名。
- 门外运行 `python tests/tabbit_e2e_evidence.py`；单跑可加 `--suite tools/e2e/scenario_mouse.sh`。入口只启动自己的 loopback 31xx 实例，给 runtime 和 projects 各建独立临时目录，每个场景新建 `e2e-tabbit-` 项目，最后停止实例并删除临时目录。不接用户实例，不从 `/api/healthz` 找项目。
- 日志、逐场景截图与 `summary.json` 落在 `.qa/artifacts/tabbit/` 的本次目录（可用 `--evidence-dir` 改根）。报告区分 PASS / FAIL / SKIP，`KNOWN-GAP` 不计入 PASS。离线夹具不发起外部生成/对话；没有本地服务商时必须报告缺口。源码检查单列。
- `tools/e2e/scenario_*.sh` 自动注册到入口，每个套件有独立超时。不安装 Tabbit 的合成门环境只报 `NOT_RUN`（见 #310），不是浏览器通过。实际验收以门外的截图和报告为准。

## Wayfinding

`/wayfinder` 使用一个带 `wayfinder:map` 标签的 Issue 作为总地图，子 Issue 作为决策任务。

- 子任务使用 `wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`
- 优先使用 GitHub 原生子 Issue 和依赖关系
- 不支持原生依赖时，在正文顶部写 `Blocked by: #<number>`
- 只有阻塞任务全部关闭后，任务才可实施
- 领取任务时分配给当前维护者
- 完成后添加结论评论并关闭 Issue
