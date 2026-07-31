# 萤火画布 PS 连接器

这是核心画布与同一台电脑上 Photoshop 之间的最小往返面板。核心画布图片节点的浮动工具栏提供“发送到 PS”，连接器通过本机 WebSocket 自动接收绑定并置入来源图；面板也保留显式填写来源 ID 的回退入口。它只负责连接状态、发送指定项目来源图、接收当前合成图/图层/选区，并展示一次性请求状态。

## 连接边界

- 服务地址默认 `127.0.0.1:3000`；协议服务端只接受真实 loopback peer，局域网地址不会被接受。
- 连接器以 `ps_*` 客户端 ID 订阅 `/ws/stats`，断线后只重连健康检查和状态通道，不会重复创建或重放往返请求。
- 画布先创建绑定 `project_id`、`source_node_id`、`request_id` 的请求；连接器只携带这些标识，不选择项目路径、媒体 ID 或节点 ID。
- 返回数据携带显示名称、SHA-256 和 base64 媒体字节。服务端校验后在项目目录保存新项目媒体，创建新的 `ps_edit_result` 节点和 `derived_from` 边，绝不覆盖来源。
- 请求只允许 `pending → returned` 一次转移；未知、错误项目/来源、重复、过期、路径名称和哈希不匹配都会被拒绝。

## 开发

使用 Adobe UXP Developer Tool 加载本目录的 `manifest.json`，在 Photoshop 24.0 以上版本打开面板。面板不读取全局素材，也不执行模型或命令行任务。

## 协议端点

```text
POST /api/project-library/{project_id}/photoshop/round-trips
GET  /api/project-library/{project_id}/photoshop/round-trips/{request_id}/source
GET  /api/project-library/{project_id}/photoshop/round-trips/{request_id}
POST /api/project-library/{project_id}/photoshop/round-trips/{request_id}/return
WS   /ws/stats?client_id=ps_<connector-id>
```
