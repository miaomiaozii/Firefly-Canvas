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

PARAM_REF_ROLE = {"type": "string", "enum": ["product", "detail", "layout", "scene_style", "general"]}
_REF_SOURCE_PROPERTIES = {
    "asset_id": {"type": "string", "minLength": 1},
    "node_id": {"type": "string", "minLength": 1},
    "image_index": {"type": "integer", "minimum": 0},
}
PARAM_REF_SOURCE = {
    "type": "object",
    "description": "二选一：资源库 asset_id，或来源节点 node_id 与可选的 image_index（默认 0）",
    "properties": _REF_SOURCE_PROPERTIES,
    "additionalProperties": False,
    "oneOf": [
        {"type": "object", "properties": {k: _REF_SOURCE_PROPERTIES[k] for k in keys},
         "required": [keys[0]], "additionalProperties": False}
        for keys in (["asset_id"], ["node_id", "image_index"])
    ],
}

VIEW_TIMEOUT_NOTE = "慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s"

# Host settings and bindings are not image runSettings. Frame items remain server-managed.
from app.services.agent_image_settings import RUN_SETTINGS_PROPERTIES

LAYOUT_LIMIT = 10_000_000
_LAYOUT_ID = {"type": "string", "minLength": 1, "maxLength": 500}
_LAYOUT_IDS = {"type": "array", "items": _LAYOUT_ID, "maxItems": 500, "uniqueItems": True}
_LAYOUT_NUMBER = {"type": "number", "minimum": -LAYOUT_LIMIT, "maximum": LAYOUT_LIMIT}
_LAYOUT_GAP = {**_LAYOUT_NUMBER, "minimum": 0}
_LAYOUT_INCLUDE_USER = {"type": "boolean", "description": "仅当用户明确点名 node_ids 中的用户节点时设为 true；只有无需确认档且本轮有相应的用户节点引用可免确认。"}


def _layout_enum(*values):
    return {"type": "string", "enum": list(values)}


def _layout_schema(required, **properties):
    return {"type": "object", "required": required, "properties": properties, "additionalProperties": False}


LAYOUT_SCHEMAS = {
    "canvas.group_create": _layout_schema(["title"], title={**_LAYOUT_ID, "maxLength": 200}, node_ids=_LAYOUT_IDS,
        plan_section={**_LAYOUT_ID, "maxLength": 200}, include_user_nodes=_LAYOUT_INCLUDE_USER,
        anchor={"oneOf": [_layout_schema(["x", "y"], x=_LAYOUT_NUMBER, y=_LAYOUT_NUMBER),
                         _layout_enum("right_of_selection", "auto")]}),
    "canvas.group_arrange": _layout_schema(["group_id", "layout"], group_id=_LAYOUT_ID,
        layout=_layout_enum("grid", "row", "column"), sort_by=_layout_enum("plan_order", "title", "current"), gap=_LAYOUT_GAP),
    "canvas.arrange_groups": _layout_schema(["group_ids", "direction"], group_ids=_LAYOUT_IDS,
        direction=_layout_enum("row", "column"), gap=_LAYOUT_GAP),
    "canvas.align": _layout_schema(["node_ids", "edge"], node_ids=_LAYOUT_IDS,
        edge=_layout_enum("left", "center_x", "right", "top", "center_y", "bottom"), include_user_nodes=_LAYOUT_INCLUDE_USER),
    "canvas.distribute": _layout_schema(["node_ids", "axis"], node_ids=_LAYOUT_IDS,
        axis=_layout_enum("x", "y"), gap=_LAYOUT_GAP, include_user_nodes=_LAYOUT_INCLUDE_USER),
}
LAYOUT_TOOLS = frozenset(LAYOUT_SCHEMAS)

_LAYOUT_DESCRIPTIONS = {
    "canvas.group_create": "新建显式排版框；组员及其连线保持独立。同标题、同 Agent 的排版框复用（不增加成员）；不转换旧分组，不收已入组节点，不允许嵌套。anchor 默认 auto（画布右侧）；也可指定 right_of_selection 或 {x,y}。默认跳过受保护节点。",
    "canvas.group_arrange": "按 grid（最多三列）、row 或 column 排列新建排版框的自有组员。sort_by 默认 current（先上后下、先左后右）；受保护组员原地保留，旧分组和会话框不支持。",
    "canvas.arrange_groups": "按传入顺序并排或竖排显式排版框；成员随框平移。跳过旧框、受保护框及含受保护成员的框。",
    "canvas.align": "将可移动卡片向首个可移动目标的指定边缘/中心对齐，默认跳过受保护节点；整组平移请用 arrange_groups。",
    "canvas.distribute": "沿指定轴在最外侧卡片之间等距分布（至少三张）；指定 gap 时从首张开始定距排列（至少两张）。默认跳过受保护节点。",
}

UPDATABLE_NODE_FIELDS = frozenset({
    "title", "text", "prompt", "x", "y", "w", "h", "status", "runSettings",
})

TOOLS = [
    {"name": "ops.list", "description": "列出当前项目的 Agent 操作日志，可按 turn_id 筛选；外部 MCP 的 turn_id 不得复用，未提供轮次时只可逐条撤销。", "inputSchema": {"type": "object", "properties": {"turn_id": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}, "required": [], "additionalProperties": False}},
    {"name": "ops.undo", "description": "按 op_id 或 turn_id 撤销（恰好提供一个）；外部 MCP 的 turn_id 不得复用，未提供轮次时不支持按轮撤销。", "inputSchema": {"type": "object", "properties": {"op_id": {"type": "string", "minLength": 1}, "turn_id": {"type": "string", "minLength": 1}}, "required": [], "additionalProperties": False}},
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
    {"name": "canvas.update_node", "description": "更新节点属性并返回 prev；runSettings 只合并指定的生图字段，仅可改未被人工编辑的自有 Agent 节点的生成参数", "inputSchema": {"type": "object", "properties": {
        "node_id": {"type": "string"},
        "title": {"type": "string", "description": "新标题"},
        "text": {"type": "string", "description": "新正文"},
        "prompt": {"type": "string", "description": "新提示词"},
        "x": {"type": "number", "description": "画布 X 坐标"},
        "y": {"type": "number", "description": "画布 Y 坐标"},
        "w": {"type": "number", "description": "节点宽度"},
        "h": {"type": "number", "description": "节点高度"},
        "runSettings": {"type": "object", "properties": RUN_SETTINGS_PROPERTIES, "additionalProperties": False},
    }, "required": ["node_id"], "additionalProperties": False}},
    {"name": "canvas.delete_node", "description": "删除节点（破坏性：需画布确认门批准，阻塞最长 90s）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
    # #356 D12：connect 统一平铺 from_node/to_node/kind；旧 edge 对象 handler 兼容一版。
    {"name": "canvas.connect", "description": "创建节点连线（平铺 from_node/to_node，kind 可选缺省 flow；连线 id 服务端铸造。旧 edge 对象参数已弃用，下个版本移除）", "inputSchema": {"type": "object", "properties": {
        "from_node": {"type": "string", "description": "起点节点 id"},
        "to_node": {"type": "string", "description": "终点节点 id"},
        "kind": {"type": "string", "description": "连线类型，可选，缺省 flow"},
    }, "required": ["from_node", "to_node"], "additionalProperties": False}},
    {"name": "canvas.disconnect", "description": "删除节点连线（破坏性：需画布确认门批准，阻塞最长 90s）", "inputSchema": {"type": "object", "properties": {"from_node": {"type": "string"}, "to_node": {"type": "string"}}, "required": ["from_node", "to_node"], "additionalProperties": False}},
    {"name": "canvas.place_media", "description": "将项目资源库中的媒体放到画布上，可指定目标节点、组框或标题。", "inputSchema": {
        "type": "object", "properties": {
            "asset_id": {"type": "string", "minLength": 1, "description": "项目媒体资源 ID"},
            "node_id": {"type": "string", "minLength": 1, "description": "目标节点 ID（可选）"},
            "group_id": {"type": "string", "minLength": 1, "description": "目标组框 ID（可选）"},
            "title": {"type": "string", "description": "节点标题（可选）"},
        }, "required": ["asset_id"], "additionalProperties": False,
    }},
    {"name": "canvas.attach_refs", "description": "为目标节点挂参考图。replace 只替换 Agent 标记的参考图，绝不替换用户的参考图。", "inputSchema": {
        "type": "object", "properties": {
            "node_id": {"type": "string", "minLength": 1, "description": "目标节点 ID"},
            "refs": {"type": "array", "items": {
                "type": "object", "properties": {
                    "source": PARAM_REF_SOURCE,
                    "role": {**PARAM_REF_ROLE, "default": "general", "description": "参考图角色，省略时为 general"},
                    "enabled": {"type": "boolean", "description": "是否启用该参考图（可选）"},
                }, "required": ["source"], "additionalProperties": False,
            }},
            "mode": {"type": "string", "enum": ["append", "replace"], "default": "append",
                     "description": "append 追加（默认）；replace 只替换 Agent 标记的参考图"},
        }, "required": ["node_id", "refs"], "additionalProperties": False,
    }},
    {"name": "canvas.set_ref_order", "description": "调整目标节点的参考图顺序、角色，或同时调整两者。", "inputSchema": {
        "type": "object", "properties": {
            "node_id": {"type": "string", "minLength": 1, "description": "目标节点 ID"},
            "order": {"type": "array", "items": {"type": "string", "minLength": 1},
                      "description": "按目标顺序列出的参考图资源 ID"},
            "role_overrides": {"type": "object", "additionalProperties": PARAM_REF_ROLE,
                               "description": "参考图资源 ID → 角色（product/detail/layout/scene_style/general）"},
        }, "required": ["node_id"], "anyOf": [{"required": ["order"]}, {"required": ["role_overrides"]}],
        "additionalProperties": False,
    }},
    *[{"name": name, "description": description, "inputSchema": LAYOUT_SCHEMAS[name]}
      for name, description in _LAYOUT_DESCRIPTIONS.items()],
    {"name": "canvas.search_projects", "description": "跨项目搜索（只读，票 F/ADR-0026）：按关键词搜所有项目画布节点的标题/prompt/正文与项目名，返回按项目分组命中（node_id/kind/field/snippet）；不搜 agent 会话内容，不建索引", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200, "description": "搜索关键词（大小写不敏感）"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "命中总数上限（默认 50）"}}, "required": ["query"], "additionalProperties": False}},
    # #266 只读视图工具（ComfyArt §59.4 语义；只读不走确认门——#242 语义：写操作才需确认）
    {"name": "canvas.view", "description": f"整页/选区节点媒体导出为一张图（contact sheet，以 MCP image 内容块返回，文本块仅含元数据）喂模型（只读）。{VIEW_TIMEOUT_NOTE}；超时时先调低 max_px 或改用 selection 缩小批量，再重试", "inputSchema": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["page", "selection"], "description": "page=整页（所有带媒体节点）；selection=指定 node_ids"}, "node_ids": PARAM_NODE_IDS, "max_px": PARAM_MAX_PX}, "required": ["scope"], "additionalProperties": False}},
    {"name": "canvas.view_media", "description": f"查看单节点媒体：图像下缩放，视频抽帧拼图（contact sheet）（只读）。{VIEW_TIMEOUT_NOTE}", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "max_px": PARAM_MAX_PX, "frames": {"type": "integer", "description": "视频抽帧数 1..16，默认 4", "minimum": 1, "maximum": 16}}, "required": ["node_id"], "additionalProperties": False}},
    # #266 edit_* 首批（包装 canvas-image-editor.js 已有能力）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_crop", "description": "裁剪节点图像选区并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "rect": PARAM_RECT}, "required": ["node_id", "rect"], "additionalProperties": False}},
    {"name": "canvas.batch_prompt_edit", "description": "批量编辑文本或图像/视频节点的提示词，不触发生图。先以 apply:false + edits 预览，再以 apply:true + diff_id 应用。浏览器整批确认（含用户改过的提示词），取消零写入；每个预览只可尝试应用一次。", "inputSchema": {
        "type": "object", "properties": {
            "apply": {"type": "boolean"}, "diff_id": {"type": "string", "minLength": 1},
            "edits": {"type": "array", "minItems": 1, "maxItems": 50, "items": {
                "type": "object", "properties": {
                    "node_id": {"type": "string", "minLength": 1},
                    "prompt": {"type": "string"}, "append": {"type": "string"},
                    "find": {"type": "string", "minLength": 1}, "replace": {"type": "string"},
                }, "required": ["node_id"], "additionalProperties": False,
                "oneOf": [{"required": ["prompt"]}, {"required": ["append"]},
                          {"required": ["find", "replace"]}],
            }},
        }, "required": ["apply"], "additionalProperties": False,
        "oneOf": [{"properties": {"apply": {"const": False}}, "required": ["edits"],
                   "not": {"required": ["diff_id"]}},
                  {"properties": {"apply": {"const": True}}, "required": ["diff_id"],
                   "not": {"required": ["edits"]}}],
    }},
    # #266 canvas.batch_edit（§59.3）：选中 N 节点 + 一句 prompt = N 次生成（调既有生成通路，非本地处理；结果放各自旁边）
    {"name": "canvas.batch_edit", "description": "批量生成：把同一句 prompt 写入最多 10 个选中节点并逐节点触发一次生成（消耗外部生成任务，区别于 edit_* 的本地处理；结果放各自旁边 = 各节点自身输出）。总是请求浏览器整批确认提示词覆盖和生图费用（阻塞最长 90s），每 agent 会话按张数计入生成上限；返回各节点的 task_id，用 generate.status 轮询 / generate.wait 等待完成", "inputSchema": {"type": "object", "properties": {"node_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10, "description": "节点 ID 列表（1..10）"}, "prompt": {"type": "string", "minLength": 1, "description": "批量 prompt，写入每个节点"}}, "required": ["node_ids", "prompt"], "additionalProperties": False}},
    # #266b edit_* 再扩两项（包装 canvas-image-editor.js 已有能力：rotate 是既有能力，flip 同族本地变换）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_rotate", "description": "旋转节点图像（90/180/270 逆时针）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "angle": {"type": "integer", "enum": [90, 180, 270], "description": "逆时针旋转角度，90/180/270 之一"}}, "required": ["node_id", "angle"], "additionalProperties": False}},
    {"name": "canvas.edit_flip", "description": "镜像节点图像（horizontal 左右 / vertical 上下）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "direction": {"type": "string", "enum": ["horizontal", "vertical"], "description": "horizontal=左右镜像，vertical=上下镜像"}}, "required": ["node_id", "direction"], "additionalProperties": False}},
    {"name": "canvas.edit_outpaint", "description": "复刻站内红底扩图链：扩大画布用高饱和红（#ff0044）填充、原图按 offset 贴回，覆写节点图（本地 PIL，不消耗外部生成；外区红底供 generate.trigger 重绘填充，不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "outpaint_size": {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}, "required": ["width", "height"], "additionalProperties": False, "description": "目标画布尺寸（像素；须大于原图像素）"}, "offset": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "additionalProperties": False, "description": "原图在新画布中的位置（像素，默认 0,0 左上角）"}}, "required": ["node_id", "outpaint_size"], "additionalProperties": False}},
    {"name": "canvas.edit_erase", "description": "按 prompt 擦除节点图选区内容：裁出选区送 bailian 局部编辑模型（qwen-image-edit-max-2026-01-16）重绘后合成回节点图（消耗一次外部模型调用；不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "rect": PARAM_RECT, "prompt": {"type": "string", "minLength": 1, "description": "描述要擦除什么、如何填充该区域"}}, "required": ["node_id", "rect", "prompt"], "additionalProperties": False}},
    {"name": "generate.trigger", "description": "按节点保存的 runSettings 和画布参考图生图，返回 task_id。参考图必须已归档到当前项目。按本次请求图片张数确认和计入会话额度（阻塞最长 90s，autonomous 自动放行）；结果写入项目历史", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"], "additionalProperties": False}},
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
    *LAYOUT_TOOLS,
    "canvas.attach_refs", "canvas.batch_edit", "canvas.batch_prompt_edit", "canvas.connect", "canvas.create_node",
    "canvas.delete_node", "canvas.disconnect", "canvas.edit_crop", "canvas.edit_erase",
    "canvas.edit_flip", "canvas.edit_outpaint", "canvas.edit_rotate", "canvas.place_media",
    "canvas.set_ref_order", "canvas.update_node",
    "generate.trigger", "media.delete", "media.upload",
})

# #393：共享描述须明确限定在外部 MCP；turn_id 不进写工具的 JSON schema，Pi 由服务端归属轮次。
_EXTERNAL_MCP_TURN_NOTE = (
    "仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；"
    "同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。"
)
for _tool in TOOLS:
    if _tool["name"] in WRITE_TOOLS:
        _tool["description"] = f"{_tool['description'].rstrip('。')}。{_EXTERNAL_MCP_TURN_NOTE}"
