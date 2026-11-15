# Firefly Canvas 测试报告

更新时间：2026-08-05

## 范围

本轮从用户操作路径验证画布、项目、历史、资产、设置、生成和节点编排，同时做了后端数据一致性、并发和边界审查。浏览器测试使用真实 Chrome，不把 Python 单测结果当作 UI 通过依据。

## 自动化结果

### Python

- `python -m unittest discover -s tests -p "test*.py"`
- 结果：368 tests passed，1 skipped，0 failures，0 errors。
- 本轮历史事务、UI 契约、banana 请求、Photoshop connector 额外定向测试：37 tests passed。

### 浏览器

已验证的浏览器套件包括：

- 画布基础：`core_surface`、`canvas_experience`、`canvas_interactions`、`canvas_boundaries`、`undo_redo`
- 节点与连线：`node_e2e`、`node_flow`、`local_edit`、`conversation_ui`、`agent_ui`、`agent_deps`
- 生成链路：`connectivity`、`generation`、`generation_failure`、`video_failure`
- 页面 UI：`project_list`、`global_settings`、`settings`、`asset_library`、`history_drawer`
- 编辑与导航：`image_editor`、`image_editor_func`、`canvas_navigation`、`photoshop_connector`
- 并发与恢复：`dual_tab_sync`、`crash_recovery`、`p0_regressions`、`perf_drag`、`perf500_long_browser`

最近一次直接回归通过：

- `connectivity_browser.py`：模型、比例、分辨率、质量、自动路由、参考图、生成下载和四类节点创建。
- `canvas_experience_browser.py`：视口外节点提示、跳转到节点和结果提示。
- `undo_redo_browser.py`：节点编辑、撤销、重做和保存收敛。
- `image_editor_func_browser.py`：crop、brush、mask、outpaint、grid、resize、服务端持久化。
- `dual_tab_sync_browser.py`：两个 Chrome 页面同时编辑同一画布、WebSocket 广播、冲突重放、无重复节点。
- `project_list_browser.py`：桌面/移动双视口验证 Fly 导出、浏览器 FormData 导入、导入项目读取、删除、恢复和 purge。
- `crash_recovery_browser.py`：pagehide 触发防抖窗口内保存，重新打开后节点和文本恢复；保存冲突由前端重放链处理。
- `perf500_long_browser.py`：500 个图片节点与真实 6–20MB 素材，500 张图加载、拖动、保存收敛和 reload 后无重复节点；图片加载约 0.93 秒，拖动到 dirty 约 0.15 秒，保存约 10.1 秒。

## 页面 UI 验收

- UI Foundation：画布、全局设置和项目列表共用 variables-only token 层；accent 为 `#5879dc`，字体刻度为 10/11/12/13/14/16/20/page clamp，panel/card/utility radius 为 12/8/6。三个页面均无多余 gradient 和重阴影。
- 画布：左上唯一八控件 icon-only 工具栏，保存固定最右；右上 minimap、单层 24px radial dots、保存状态、composer、节点、边缘提示、缩放/平移、空画布状态均有桌面和移动路径；图片 toolbar/filename/image 几何间距为 8px/6px。快捷键在左侧展开：桌面作为临时工作抽屉让画布内容右移，移动端按可见节点选择纵向位置。
- Composer：CSS 是宽度单一来源，JS 使用真实 DOM rect 定位；标准图片六组参数和运行按钮在桌面一行，参数区无横向滚动条，移动端按断点换行。Prompt 默认高度为 84px，below/above/left/right 候选按重叠面积选择，并在初始化取景、窗口 resize 后重新定位。
- 历史：无分页的水平虚拟列表，5000 条逻辑记录只保持最多 140 张卡 DOM；隐藏滚动条，滚轮、deltaX、方向键、Home/End、触控和焦点卡均可用。图片优先竖卡、缩略图严格居中、紧凑元信息、三个按钮、克制失败状态、复制 prompt、发送到画布携带完整生成参数已验证。
- 历史删除补充验证：删除按钮以一个事务同时删除记录和生成原图；用户导入媒体保留，引用节点变为可重生成状态，trash/purge 与跨项目隔离通过。
- 历史失败态补充验证：失败原因默认不占空间，卡片 hover/focus 时在 `.log-main` 内显示并截断；没有浏览器原生长 tooltip，也不会覆盖相邻卡片。
- 资产库：面板打开/关闭、项目/全局/本地来源、分类切换、资产网格和拖拽入口已验证。
- 全局设置：偏好、服务商卡片、模型搜索、CLI、项目库和 Photoshop 区域已验证；模型搜索是模糊匹配；text/select 34px、textarea 90px、checkbox 原生，dark/light 与 mobile 单列均通过。
- 项目列表：项目切换、Fly 导入导出、trash/purge 和跨画布导航已验证；desktop 多列、mobile 单列，dark/light 均通过。
- 图片编辑器：不仅打开模式，还验证了实际编辑结果和最终画布保存。
- 对话与 Agent：发送、回复、参考图、失败重试、依赖级联、取消和重试按钮已验证；Agent goal、任务草稿、prompt、依赖和动作区使用统一深色表单样式，无原生白底控件。

## 本轮发现并修复

- `.model-option[hidden]` 和 `.direct-option[hidden]` 显式覆盖 `display:flex`，修复设置页及画布模型搜索不筛选的问题。
- composer 改用真实渲染节点 `getBoundingClientRect()` 和 world transform，修复缩放/平移后运行按钮被遮挡。
- 窄屏首次打开时，如果恢复的桌面 viewport 让节点不可读，自动重新取景；桌面用户已有平移不被覆盖。
- 修复快捷键面板固定右侧及遮挡节点的问题；桌面改为左侧临时抽屉，移动端保持覆盖式面板但避开顶部工具栏和小地图。
- 修复 Composer 提示词区域过高、参数行挤压、打开或 resize 后遮挡节点/停在视口外，以及 Agent 任务字段退化为浏览器原生白底控件的问题。
- 生成历史改为缩略图居中的图片优先竖向卡片；标题/说明收紧，底部使用无重边框的三按钮 rail；失败卡取消大面积红框/红底并修复状态 chip 文字挤压；发送到画布保留 prompt/model/size/quality 等生成信息，删除改为历史+原图单事务，并让历史立即写盘。
- 修复批量 CSS 替换留下的 `marginauto`、`paddingauto`、`margin-18px` 无效声明。
- Agent 取消/重试使用任务节点的 `agent_id`，并在服务端成功后同步画布节点状态。

## 真实截图自检

- 截图矩阵：画布、全局设置、项目列表均完成 desktop 1424×900 / mobile 500×844 × dark/light 人工读取。
- 画布：工具栏/minimap 不重叠，点阵无灰块，历史抽屉、竖卡、Composer、Agent、图片节点和连线均无遮挡；快捷抽屉桌面/移动截图分别为 `shortcut-left-drawer-desktop.png` 与 `shortcut-left-drawer-mobile.png`。
- 设置与项目：dark/light 表面、正文、主按钮和表单均有清晰对比；mobile document 不横向溢出。
- 截图由浏览器工具直接查看，没有使用 Vision 或图像识别服务。

## 仍需单独验证

- 真实 Photoshop 客户端连接，目前是 fake adapter；需要真实 UXP 宿主才能完成最终闭环。
- 长时间连续生成、持续数小时拖拽帧率和更高并发的压力测试。
- 可灵剩余协议、更多 OpenAI 图片模型等按需扩展；音频适配按用户确认暂不做。
