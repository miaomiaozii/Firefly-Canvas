# 萤火画布 PS 连接器

在萤火画布服务与同一台机器上的 Photoshop 之间建立安全小桥。画布图片节点的编辑往返由服务端维护，连接器通过 WebSocket 自动接收绑定并放置源图，也可以手动填写源 ID 后发送。它只处理往返状态，不会在指定项目之外读取任何资源。

## 连接边界

- 服务地址默认 `127.0.0.1:3000`，协议层只接受真实 loopback peer，非本机地址会被拒绝。
- 连接器以 `ps_*` 客户端 ID 接入 `/ws/stats`，服务端只对它广播往返状态通道，绝不重放其他消息。
- 一次往返只携带 `project_id`、`source_node_id`、`request_id` 三个标识符，不携带完整项目路径、媒体 ID 或节点 ID。
- 返回请求携带显示名称、SHA-256 与 base64 媒体字节；服务端校验后写入项目目录，新建 `ps_edit_result` 节点并以 `derived_from` 边连接原节点。
- 往返只在 `pending → returned` 之间做一次转移；未知项目/来源、重复返回、路径名称和哈希匹配违规都会被拒绝。

## 安装

使用 Adobe UXP Developer Tool 打开本目录的 `manifest.json`，在 Photoshop 24.0 及以上版本加载。面板不抓取全站素材，也不执行模型推理。

## 协议端点

```text
POST /api/project-library/{project_id}/photoshop/round-trips
GET  /api/project-library/{project_id}/photoshop/round-trips/{request_id}/source
GET  /api/project-library/{project_id}/photoshop/round-trips/{request_id}
POST /api/project-library/{project_id}/photoshop/round-trips/{request_id}/return
WS   /ws/stats?client_id=ps_<connector-id>
```
