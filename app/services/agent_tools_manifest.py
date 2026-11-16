# -*- coding: utf-8 -*-
"""Agent 工具面单一来源清单（#356，ADR 0022）。

MCP streamable-HTTP 端（app/routers/api_mcp.py）直接 import 本模块的 TOOLS；
pi 扩展的工具定义由 tools/gen_canvas_tools.py 从本清单生成到独立文件
app/agent_extensions/canvas-tools.generated.ts（重跑 diff 为空进 pytest：
tests/test_tool_surface_consistency.py）；canvas-tools.ts 保留手写执行器。
两边共享同一实现：MCP 路径
直达 core，pi 路径经 REST 孪生（app/routers/api_agent_tools.py）打同一批 core。

统一口径（#356 拍板 D11/D12/D13）：
- create_node/update_node/connect 采用平铺参数（原 MCP 的 node/updates/edge 自由
  对象形状在 handler 里保留一版兼容，description 标注已弃用）。
- 客户端不做参数默认值：x/y 缺省由服务端 frame 自动布局；edge id / node id 由
  服务端铸造；kind 缺省 "flow" 归服务端 core。
- 字段白名单（#355）将在本清单的 create_node/update_node 条目上扩 `properties`
  收紧，两边自动跟随——改这里之前先看 #355。
"""
from app.services.canvas_view import DEFAULT_VIEW_MAX_PX, MAX_VIEW_MAX_PX

#: 公共参数片段（#266 票面：公共参数片段 + additionalProperties:false）
PARAM_MAX_PX = {
    "type": "integer",
    "description": f"最长边像素，默认 {DEFAULT_VIEW_MAX_PX}，上限 {MAX_VIEW_MAX_PX}（ComfyArt maxPx 契约）",
    "minimum": 64,
    "maximum": MAX_VIEW_MAX_PX,
}
PARAM_NODE_IDS = {"type": "array", "items": {"type": "string"}, "description": "节点 ID 列表"}
PARAM_RECT = {
    "type": "object",
    "description": "选区（原图像素坐标，x/y 左上角 + width/height）",
    "properties": {
        "x": {"type": "number"}, "y": {"type": "number"},
        "width": {"type": "number"}, "height": {"type": "number"},
    },
    "required": ["x", "y", "width", "height"],
    "additionalProperties": False,
}

VIEW_TIMEOUT_NOTE = "慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s"

# #355 W1：canvas.update_node / REST PATCH /canvas/nodes/{id} 可更新字段白名单——
# 两路共用同一份（update_node_core 强制）。故意不含：images、model、agentCli、
# extSessionId、autonomy、chatFrameId、mcp_session 等会话/生成/绑定字段（票面点名）。
# id/type/created_at 在 core 里另有身份保护（命中白名单外同样被拒）。
# #355：update_node 可更新字段白名单（单一来源，REST/MCP 两路共用；白名单外 400）。
# 不含 `items`：frame 成员列表由 frame/连边端点专管，开放给 update_node 会绕过
# 成员一致性检查（review 01:08 口径：无正当理由，删）。
UPDATABLE_NODE_FIELDS = frozenset({
    "title", "text", "prompt", "x", "y", "w", "h", "status",
})

TOOLS = [
    {"name": "canvas.list_nodes", "description": "列出画布节点与组索引（含连边、创建来源和人工保护摘要）；可按 group_id 直接成员或 created_by 原创建来源筛选。fields 省略时返回 summary/prompt/refs/run 全部四块，[] 只返回 summary；summary 始终返回。", "inputSchema": {"type": "object", "properties": {
        "group_id": {"type": "string", "description": "组框 ID，仅列出该组的直接成员（不递归）"},
        "created_by": {"type": "string", "enum": ["agent", "user"], "description": "按原创建来源筛选，不按当前接管者筛选"},
        "fields": {"type": "array", "items": {"type": "string", "enum": ["summary", "prompt", "refs", "run"]}, "description": "返回块：省略=全部；[]=仅 summary；summary 始终返回"},
    }, "required": [], "additionalProperties": False}},
    {"name": "canvas.get_node", "description": "获取单个节点详情", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
    # #356 D11：平铺形状；x/y 缺省=服务端 frame 自动布局；id 服务端铸造。#355 将在
    # 此条目收紧 properties（字段白名单）。
    {"name": "canvas.create_node", "description": "创建节点：type 必填（smart-prompt/smart-image/smart-video/smart-agent/conversation）；title/text/prompt 可选；x/y 省略时由服务端按发起 agent 的会话 frame 网格自动落位，显式给值则尊重；节点 id 一律服务端铸造。", "inputSchema": {"type": "object", "properties": {
        "type": {"type": "string", "description": "节点类型：smart-prompt/smart-image/smart-video/smart-agent/conversation"},
        "title": {"type": "string", "description": "节点标题"},
        "x": {"type": "number", "description": "X 位置（省略=服务端自动落位）"},
        "y": {"type": "number", "description": "Y 位置（省略=服务端自动落位）"},
        "text": {"type": "string", "description": "正文（prompt 类节点）"},
        "prompt": {"type": "string", "description": "提示词（image/video 节点）"},
    }, "required": ["type"], "additionalProperties": False}},
    # #356 D11：平铺形状（node_id + 可更新字段）；旧 `updates` 自由对象 handler 兼容
    # 一版已弃用。#355 将在此条目落实字段白名单。
    {"name": "canvas.update_node", "description": "更新节点属性（平铺字段：node_id + title/text/prompt 等可更新字段；旧 updates 自由对象已弃用）", "inputSchema": {"type": "object", "properties": {
        "node_id": {"type": "string"},
        "title": {"type": "string", "description": "新标题"},
        "text": {"type": "string", "description": "新正文"},
        "prompt": {"type": "string", "description": "新提示词"},
    }, "required": ["node_id"], "additionalProperties": False}},
    {"name": "canvas.delete_node", "description": "删除节点（破坏性：需画布确认门批准，阻塞最长 90s）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
    # #356 D12：connect 统一平铺 from_node/to_node/kind；旧 edge 对象 handler 兼容一版。
    {"name": "canvas.connect", "description": "创建节点连线（平铺 from_node/to_node，kind 可选缺省 flow；连线 id 服务端铸造。旧 edge 对象参数已弃用，下个版本移除）", "inputSchema": {"type": "object", "properties": {
        "from_node": {"type": "string", "description": "起点节点 id"},
        "to_node": {"type": "string", "description": "终点节点 id"},
        "kind": {"type": "string", "description": "连线类型，可选，缺省 flow"},
    }, "required": ["from_node", "to_node"], "additionalProperties": False}},
    {"name": "canvas.disconnect", "description": "删除节点连线（破坏性：需画布确认门批准，阻塞最长 90s）", "inputSchema": {"type": "object", "properties": {"from_node": {"type": "string"}, "to_node": {"type": "string"}}, "required": ["from_node", "to_node"], "additionalProperties": False}},
    {"name": "canvas.search_projects", "description": "跨项目搜索（只读，票 F/ADR-0026）：按关键词搜所有项目画布节点的标题/prompt/正文与项目名，返回按项目分组命中（node_id/kind/field/snippet）；不搜 agent 会话内容，不建索引", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200, "description": "搜索关键词（大小写不敏感）"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "命中总数上限（默认 50）"}}, "required": ["query"], "additionalProperties": False}},
    # #266 只读视图工具（ComfyArt §59.4 语义；只读不走确认门——#242 语义：写操作才需确认）
    {"name": "canvas.view", "description": f"整页/选区节点媒体导出为一张图（contact sheet，以 MCP image 内容块返回，文本块仅含元数据）喂模型（只读）。{VIEW_TIMEOUT_NOTE}；超时时先调低 max_px 或改用 selection 缩小批量，再重试", "inputSchema": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["page", "selection"], "description": "page=整页（所有带媒体节点）；selection=指定 node_ids"}, "node_ids": PARAM_NODE_IDS, "max_px": PARAM_MAX_PX}, "required": ["scope"], "additionalProperties": False}},
    {"name": "canvas.view_media", "description": f"查看单节点媒体：图像下缩放，视频抽帧拼图（contact sheet）（只读）。{VIEW_TIMEOUT_NOTE}", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "max_px": PARAM_MAX_PX, "frames": {"type": "integer", "description": "视频抽帧数 1..16，默认 4", "minimum": 1, "maximum": 16}}, "required": ["node_id"], "additionalProperties": False}},
    # #266 edit_* 首批（包装 canvas-image-editor.js 已有能力）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_crop", "description": "裁剪节点图像选区并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "rect": PARAM_RECT}, "required": ["node_id", "rect"], "additionalProperties": False}},
    # #266 canvas.batch_edit（§59.3）：选中 N 节点 + 一句 prompt = N 次生成（调既有生成通路，非本地处理；结果放各自旁边）
    {"name": "canvas.batch_edit", "description": "批量生成：把同一句 prompt 写入最多 10 个选中节点并逐节点触发一次生成（消耗外部生成任务，区别于 edit_* 的本地处理；结果放各自旁边 = 各节点自身输出）。#351：走服务端确认门（阻塞最长 90s），每 agent 会话按张数计入生成上限；返回各节点的 task_id，用 generate.status 轮询 / generate.wait 等待完成", "inputSchema": {"type": "object", "properties": {"node_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10, "description": "节点 ID 列表（1..10）"}, "prompt": {"type": "string", "minLength": 1, "description": "批量 prompt，写入每个节点"}}, "required": ["node_ids", "prompt"], "additionalProperties": False}},
    # #266b edit_* 再扩两项（包装 canvas-image-editor.js 已有能力：rotate 是既有能力，flip 同族本地变换）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_rotate", "description": "旋转节点图像（90/180/270 逆时针）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "angle": {"type": "integer", "enum": [90, 180, 270], "description": "逆时针旋转角度，90/180/270 之一"}}, "required": ["node_id", "angle"], "additionalProperties": False}},
    {"name": "canvas.edit_flip", "description": "镜像节点图像（horizontal 左右 / vertical 上下）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "direction": {"type": "string", "enum": ["horizontal", "vertical"], "description": "horizontal=左右镜像，vertical=上下镜像"}}, "required": ["node_id", "direction"], "additionalProperties": False}},
    {"name": "canvas.edit_outpaint", "description": "复刻站内红底扩图链：扩大画布用高饱和红（#ff0044）填充、原图按 offset 贴回，覆写节点图（本地 PIL，不消耗外部生成；外区红底供 generate.trigger 重绘填充，不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "outpaint_size": {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}, "required": ["width", "height"], "additionalProperties": False, "description": "目标画布尺寸（像素；须大于原图像素）"}, "offset": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "additionalProperties": False, "description": "原图在新画布中的位置（像素，默认 0,0 左上角）"}}, "required": ["node_id", "outpaint_size"], "additionalProperties": False}},
    {"name": "canvas.edit_erase", "description": "按 prompt 擦除节点图选区内容：裁出选区送 bailian 局部编辑模型（qwen-image-edit-max-2026-01-16）重绘后合成回节点图（消耗一次外部模型调用；不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "rect": PARAM_RECT, "prompt": {"type": "string", "minLength": 1, "description": "描述要擦除什么、如何填充该区域"}}, "required": ["node_id", "rect", "prompt"], "additionalProperties": False}},
    {"name": "generate.trigger", "description": "在节点上触发生成，立即返回 task_id（节点需已有 prompt）。#351：花钱工具走服务端确认门（阻塞最长 90s，autonomous 自动放行），每 agent 会话有生成次数上限（超限拒绝）；结果自动挂到源节点旁的结果节点并写入项目历史", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
    {"name": "generate.status", "description": "查询生成任务状态", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False}},
    {"name": "generate.wait", "description": "等待生成任务完成。可阻塞数分钟：timeout_seconds 1..600（默认 120）；客户端请求超时必须设为 >= 600s（ADR 0022 超时契约）", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "timeout_seconds": {"type": "number", "description": "1..600，默认 120", "minimum": 1, "maximum": 600}}, "required": ["task_id"], "additionalProperties": False}},
    {"name": "media.upload", "description": "导入本机媒体文件到项目资源库（file_path 为服务器本机路径）", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"], "additionalProperties": False}},
    {"name": "media.list", "description": "列出项目媒体资源", "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "media.delete", "description": "删除媒体资源（破坏性：需画布确认门批准，阻塞最长 90s）", "inputSchema": {"type": "object", "properties": {"asset_id": {"type": "string"}}, "required": ["asset_id"], "additionalProperties": False}},
    {"name": "viewport.focus", "description": "画布视口聚焦到节点（定向下发给打开该项目的标签页）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
    {"name": "viewport.zoom", "description": "设置画布缩放级别（定向下发给打开该项目的标签页）", "inputSchema": {"type": "object", "properties": {"level": {"type": "number", "minimum": 0.1, "maximum": 8.0, "description": "缩放倍率 0.1..8.0"}}, "required": ["level"], "additionalProperties": False}},
    {"name": "viewport.state", "description": "读取当前视口状态（最近一次 focus/zoom 命令）", "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
]

TOOL_NAMES = {t["name"] for t in TOOLS}

# #350：写类工具集——MCP 无 &node= 时才允许回退到共享外部节点（ensure upsert）；
# 其余只读工具绝不写画布（票面：「MCP 只读工具也 upsert 节点」）。
WRITE_TOOLS = frozenset({
    "canvas.batch_edit", "canvas.connect", "canvas.create_node", "canvas.delete_node",
    "canvas.disconnect", "canvas.edit_crop", "canvas.edit_erase", "canvas.edit_flip",
    "canvas.edit_outpaint", "canvas.edit_rotate", "canvas.update_node",
    "generate.trigger", "media.delete", "media.upload",
})
