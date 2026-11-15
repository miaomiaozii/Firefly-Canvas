// AUTO-GENERATED — 自动生成，勿手改。
// 生成命令：python tools/gen_canvas_tools.py
// 来源：app/services/agent_tools_manifest.py（#356 单一工具清单，ADR 0022）。
// 漂移由 tests/test_tool_surface_consistency.py（--check diff 为空）钉住。
import { Type } from "typebox";

export interface CanvasToolDef {
    name: string;
    description: string;
    parameters: any;
}

export const CANVAS_TOOL_DEFS: CanvasToolDef[] = [
    {
        name: "canvas.list_nodes",
        description: "列出画布上所有节点（含连边摘要）",
        parameters: Type.Object({}),
    },
    {
        name: "canvas.get_node",
        description: "获取单个节点详情",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "canvas.create_node",
        description: "创建节点：type 必填（smart-prompt/smart-image/smart-video/smart-agent/conversation）；title/text/prompt 可选；x/y 省略时由服务端按发起 agent 的会话 frame 网格自动落位，显式给值则尊重；节点 id 一律服务端铸造。",
        parameters: Type.Object({ type: Type.String({description: "节点类型：smart-prompt/smart-image/smart-video/smart-agent/conversation"}), title: Type.Optional(Type.String({description: "节点标题"})), x: Type.Optional(Type.Number({description: "X 位置（省略=服务端自动落位）"})), y: Type.Optional(Type.Number({description: "Y 位置（省略=服务端自动落位）"})), text: Type.Optional(Type.String({description: "正文（prompt 类节点）"})), prompt: Type.Optional(Type.String({description: "提示词（image/video 节点）"})) }),
    },
    {
        name: "canvas.update_node",
        description: "更新节点属性（平铺字段：node_id + title/text/prompt 等可更新字段；旧 updates 自由对象已弃用）",
        parameters: Type.Object({ node_id: Type.String(), title: Type.Optional(Type.String({description: "新标题"})), text: Type.Optional(Type.String({description: "新正文"})), prompt: Type.Optional(Type.String({description: "新提示词"})) }),
    },
    {
        name: "canvas.delete_node",
        description: "删除节点（破坏性：需画布确认门批准，阻塞最长 60s）",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "canvas.connect",
        description: "创建节点连线（平铺 from_node/to_node，kind 可选缺省 flow；连线 id 服务端铸造。旧 edge 对象参数已弃用，下个版本移除）",
        parameters: Type.Object({ from_node: Type.String({description: "起点节点 id"}), to_node: Type.String({description: "终点节点 id"}), kind: Type.Optional(Type.String({description: "连线类型，可选，缺省 flow"})) }),
    },
    {
        name: "canvas.disconnect",
        description: "删除节点连线",
        parameters: Type.Object({ from_node: Type.String(), to_node: Type.String() }),
    },
    {
        name: "canvas.search_projects",
        description: "跨项目搜索（只读，票 F/ADR-0026）：按关键词搜所有项目画布节点的标题/prompt/正文与项目名，返回按项目分组命中（node_id/kind/field/snippet）；不搜 agent 会话内容，不建索引",
        parameters: Type.Object({ query: Type.String({description: "搜索关键词（大小写不敏感）", minLength: 1, maxLength: 200}), limit: Type.Optional(Type.Integer({description: "命中总数上限（默认 50）", minimum: 1, maximum: 200})) }),
    },
    {
        name: "canvas.view",
        description: "整页/选区节点媒体导出为一张图（contact sheet，以 MCP image 内容块返回，文本块仅含元数据）喂模型（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s；超时时先调低 max_px 或改用 selection 缩小批量，再重试",
        parameters: Type.Object({ scope: Type.Union([Type.Literal("page"), Type.Literal("selection")], {description: "page=整页（所有带媒体节点）；selection=指定 node_ids"}), node_ids: Type.Optional(Type.Array(Type.String(), {description: "节点 ID 列表"})), max_px: Type.Optional(Type.Integer({description: "最长边像素，默认 1024，上限 2048（ComfyArt maxPx 契约）", minimum: 64, maximum: 2048})) }),
    },
    {
        name: "canvas.view_media",
        description: "查看单节点媒体：图像下缩放，视频抽帧拼图（contact sheet）（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s",
        parameters: Type.Object({ node_id: Type.String(), max_px: Type.Optional(Type.Integer({description: "最长边像素，默认 1024，上限 2048（ComfyArt maxPx 契约）", minimum: 64, maximum: 2048})), frames: Type.Optional(Type.Integer({description: "视频抽帧数 1..16，默认 4", minimum: 1, maximum: 16})) }),
    },
    {
        name: "canvas.edit_crop",
        description: "裁剪节点图像选区并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）",
        parameters: Type.Object({ node_id: Type.String(), rect: Type.Object({ x: Type.Number(), y: Type.Number(), width: Type.Number(), height: Type.Number() }, {description: "选区（原图像素坐标，x/y 左上角 + width/height）"}) }),
    },
    {
        name: "canvas.batch_edit",
        description: "批量生成：把同一句 prompt 写入最多 10 个选中节点并逐节点触发一次生成（消耗外部生成任务，区别于 edit_* 的本地处理；结果放各自旁边 = 各节点自身输出）。返回各节点的 task_id，用 generate.status 轮询 / generate.wait 等待完成",
        parameters: Type.Object({ node_ids: Type.Array(Type.String(), {description: "节点 ID 列表（1..10）", minItems: 1, maxItems: 10}), prompt: Type.String({description: "批量 prompt，写入每个节点", minLength: 1}) }),
    },
    {
        name: "canvas.edit_rotate",
        description: "旋转节点图像（90/180/270 逆时针）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）",
        parameters: Type.Object({ node_id: Type.String(), angle: Type.Union([Type.Literal(90), Type.Literal(180), Type.Literal(270)], {description: "逆时针旋转角度，90/180/270 之一"}) }),
    },
    {
        name: "canvas.edit_flip",
        description: "镜像节点图像（horizontal 左右 / vertical 上下）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）",
        parameters: Type.Object({ node_id: Type.String(), direction: Type.Union([Type.Literal("horizontal"), Type.Literal("vertical")], {description: "horizontal=左右镜像，vertical=上下镜像"}) }),
    },
    {
        name: "canvas.edit_outpaint",
        description: "复刻站内红底扩图链：扩大画布用高饱和红（#ff0044）填充、原图按 offset 贴回，覆写节点图（本地 PIL，不消耗外部生成；外区红底供 generate.trigger 重绘填充，不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）",
        parameters: Type.Object({ node_id: Type.String(), outpaint_size: Type.Object({ width: Type.Integer(), height: Type.Integer() }, {description: "目标画布尺寸（像素；须大于原图像素）"}), offset: Type.Optional(Type.Object({ x: Type.Optional(Type.Integer()), y: Type.Optional(Type.Integer()) }, {description: "原图在新画布中的位置（像素，默认 0,0 左上角）"})) }),
    },
    {
        name: "canvas.edit_erase",
        description: "按 prompt 擦除节点图选区内容：裁出选区送 bailian 局部编辑模型（qwen-image-edit-max-2026-01-16）重绘后合成回节点图（消耗一次外部模型调用；不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）",
        parameters: Type.Object({ node_id: Type.String(), rect: Type.Object({ x: Type.Number(), y: Type.Number(), width: Type.Number(), height: Type.Number() }, {description: "选区（原图像素坐标，x/y 左上角 + width/height）"}), prompt: Type.String({description: "描述要擦除什么、如何填充该区域", minLength: 1}) }),
    },
    {
        name: "generate.trigger",
        description: "在节点上触发生成，立即返回 task_id（节点需已有 prompt）",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "generate.status",
        description: "查询生成任务状态",
        parameters: Type.Object({ task_id: Type.String() }),
    },
    {
        name: "generate.wait",
        description: "等待生成任务完成。可阻塞数分钟：timeout_seconds 1..600（默认 120）；客户端请求超时必须设为 >= 600s（ADR 0022 超时契约）",
        parameters: Type.Object({ task_id: Type.String(), timeout_seconds: Type.Optional(Type.Number({description: "1..600，默认 120", minimum: 1, maximum: 600})) }),
    },
    {
        name: "media.upload",
        description: "导入本机媒体文件到项目资源库（file_path 为服务器本机路径）",
        parameters: Type.Object({ file_path: Type.String() }),
    },
    {
        name: "media.list",
        description: "列出项目媒体资源",
        parameters: Type.Object({}),
    },
    {
        name: "media.delete",
        description: "删除媒体资源（破坏性：需画布确认门批准，阻塞最长 60s）",
        parameters: Type.Object({ asset_id: Type.String() }),
    },
    {
        name: "viewport.focus",
        description: "画布视口聚焦到节点（定向下发给打开该项目的标签页）",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "viewport.zoom",
        description: "设置画布缩放级别（定向下发给打开该项目的标签页）",
        parameters: Type.Object({ level: Type.Number({description: "缩放倍率 0.1..8.0", minimum: 0.1, maximum: 8.0}) }),
    },
    {
        name: "viewport.state",
        description: "读取当前视口状态（最近一次 focus/zoom 命令）",
        parameters: Type.Object({}),
    },
];
