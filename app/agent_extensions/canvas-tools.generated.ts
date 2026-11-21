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
        name: "plan.import_excel",
        description: "只读解析已发送到当前 Agent 会话的 XLSX 附件。每个主图/A+ 简报只建一项，不按场景数量拆分。意向图仅供描述方向，产品图由用户自行接入。返回未勾选任务、字段识别、排除项及需复核提示。能力不包括图片识别。个人路径和链接不读取。",
        parameters: Type.Object({ attachment_id: Type.String({description: "已发送到当前 Agent 会话的附件 ID，不是文件路径或 asset_id", minLength: 32, maxLength: 32}), sheet: Type.Optional(Type.String({maxLength: 200})) }),
    },
    {
        name: "plan.edit_preview",
        description: "只编辑预览，不写画布。每个 op 传 task_id + 恰好一个动作：select、rename、merge_into、set_section。不支持拆分或挂参考图；产品参考图由用户自行连入。",
        parameters: Type.Object({ plan_id: Type.String({minLength: 1, maxLength: 128}), generation: Type.Optional(Type.Integer({description: "最近预览的 generation；传入时旧版本会被拒绝", minimum: 1})), ops: Type.Array(Type.Object({ task_id: Type.String({minLength: 1, maxLength: 128}), select: Type.Optional(Type.Boolean()), rename: Type.Optional(Type.String({maxLength: 200})), merge_into: Type.Optional(Type.String({minLength: 1, maxLength: 128})), set_section: Type.Optional(Type.String({maxLength: 200})) }), {minItems: 1, maxItems: 200}) }),
    },
    {
        name: "plan.apply",
        description: "浏览器批准确切任务后，按板块一次提交缺失任务。提示词通过 input 边连入空生图槽，不生成、不导入或连接任何参考图。缺产品图是待用户补齐的状态。只撤销本批可用返回的 op_id；task_ids 省略时使用已勾选任务。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ plan_id: Type.String({minLength: 1, maxLength: 128}), generation: Type.Optional(Type.Integer({description: "最近预览的 generation；传入时旧版本会被拒绝", minimum: 1})), layout: Type.Optional(Type.Union([Type.Literal("by_section")])), task_ids: Type.Optional(Type.Array(Type.String({minLength: 1, maxLength: 128}), {maxItems: 200, uniqueItems: true})) }),
    },
    {
        name: "ops.list",
        description: "列出当前项目的 Agent 操作日志，可按 turn_id 筛选；外部 MCP 的 turn_id 不得复用，未提供轮次时只可逐条撤销。",
        parameters: Type.Object({ turn_id: Type.Optional(Type.String({minLength: 1})), limit: Type.Optional(Type.Integer({minimum: 1, maximum: 1000})) }),
    },
    {
        name: "ops.undo",
        description: "按 op_id 或 turn_id 撤销（恰好提供一个）；外部 MCP 的 turn_id 不得复用，未提供轮次时不支持按轮撤销。",
        parameters: Type.Object({ op_id: Type.Optional(Type.String({minLength: 1})), turn_id: Type.Optional(Type.String({minLength: 1})) }),
    },
    {
        name: "canvas.list_nodes",
        description: "列出画布节点与组索引（含连边、创建来源和人工保护摘要）；可按 group_id 直接成员或 created_by 原创建来源筛选。fields 省略时返回 summary/prompt/refs/run 全部四块，[] 只返回 summary；summary 始终返回。",
        parameters: Type.Object({ group_id: Type.Optional(Type.String({description: "组框 ID，仅列出该组的直接成员（不递归）"})), created_by: Type.Optional(Type.Union([Type.Literal("agent"), Type.Literal("user")], {description: "按原创建来源筛选，不按当前接管者筛选"})), fields: Type.Optional(Type.Array(Type.Union([Type.Literal("summary"), Type.Literal("prompt"), Type.Literal("refs"), Type.Literal("run")]), {description: "返回块：省略=全部；[]=仅 summary；summary 始终返回"})) }),
    },
    {
        name: "canvas.get_node",
        description: "获取单个节点详情",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "canvas.create_node",
        description: "创建节点：type 必填（smart-prompt/smart-image/smart-video/smart-agent/conversation）；title/text/prompt 可选；x/y 省略时由服务端按发起 agent 的会话 frame 网格自动落位，显式给值则尊重；节点 id 一律服务端铸造。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ type: Type.String({description: "节点类型：smart-prompt/smart-image/smart-video/smart-agent/conversation"}), title: Type.Optional(Type.String({description: "节点标题"})), x: Type.Optional(Type.Number({description: "X 位置（省略=服务端自动落位）"})), y: Type.Optional(Type.Number({description: "Y 位置（省略=服务端自动落位）"})), text: Type.Optional(Type.String({description: "正文（prompt 类节点）"})), prompt: Type.Optional(Type.String({description: "提示词（image/video 节点）"})) }),
    },
    {
        name: "canvas.update_node",
        description: "更新节点属性并返回 prev；runSettings 只合并指定的生图字段，仅可改未被人工编辑的自有 Agent 节点的生成参数。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), title: Type.Optional(Type.String({description: "新标题"})), text: Type.Optional(Type.String({description: "新正文"})), prompt: Type.Optional(Type.String({description: "新提示词"})), x: Type.Optional(Type.Number({description: "画布 X 坐标"})), y: Type.Optional(Type.Number({description: "画布 Y 坐标"})), w: Type.Optional(Type.Number({description: "节点宽度"})), h: Type.Optional(Type.Number({description: "节点高度"})), runSettings: Type.Optional(Type.Object({ model: Type.Optional(Type.String()), provider_id: Type.Optional(Type.String()), aspect_ratio: Type.Optional(Type.String({description: "宽高比，例如 16:9"})), count: Type.Optional(Type.Integer({minimum: 1, maximum: 8})), routingMode: Type.Optional(Type.Union([Type.Literal("auto"), Type.Literal("manual")])) })) }),
    },
    {
        name: "canvas.delete_node",
        description: "删除节点（破坏性：需画布确认门批准，阻塞最长 90s）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "canvas.connect",
        description: "创建节点连线（平铺 from_node/to_node，kind 可选缺省 flow；连线 id 服务端铸造。旧 edge 对象参数已弃用，下个版本移除）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ from_node: Type.String({description: "起点节点 id"}), to_node: Type.String({description: "终点节点 id"}), kind: Type.Optional(Type.String({description: "连线类型，可选，缺省 flow"})) }),
    },
    {
        name: "canvas.disconnect",
        description: "删除节点连线（破坏性：需画布确认门批准，阻塞最长 90s）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ from_node: Type.String(), to_node: Type.String() }),
    },
    {
        name: "canvas.place_media",
        description: "将项目资源库中的媒体放到画布上，可指定目标节点、组框或标题。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ asset_id: Type.String({description: "项目媒体资源 ID", minLength: 1}), node_id: Type.Optional(Type.String({description: "目标节点 ID（可选）", minLength: 1})), group_id: Type.Optional(Type.String({description: "目标组框 ID（可选）", minLength: 1})), title: Type.Optional(Type.String({description: "节点标题（可选）"})) }),
    },
    {
        name: "canvas.attach_refs",
        description: "为目标节点挂参考图。replace 只替换 Agent 标记的参考图，绝不替换用户的参考图。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String({description: "目标节点 ID", minLength: 1}), refs: Type.Array(Type.Object({ source: Type.Object({ asset_id: Type.Optional(Type.String({minLength: 1})), node_id: Type.Optional(Type.String({minLength: 1})), image_index: Type.Optional(Type.Integer({minimum: 0})) }, {description: "二选一：资源库 asset_id，或来源节点 node_id 与可选的 image_index（默认 0）"}), role: Type.Optional(Type.Union([Type.Literal("product"), Type.Literal("detail"), Type.Literal("layout"), Type.Literal("scene_style"), Type.Literal("general")], {description: "参考图角色，省略时为 general"})), enabled: Type.Optional(Type.Boolean({description: "是否启用该参考图（可选）"})) })), mode: Type.Optional(Type.Union([Type.Literal("append"), Type.Literal("replace")], {description: "append 追加（默认）；replace 只替换 Agent 标记的参考图"})) }),
    },
    {
        name: "canvas.set_ref_order",
        description: "调整目标节点的参考图顺序、角色，或同时调整两者。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String({description: "目标节点 ID", minLength: 1}), order: Type.Optional(Type.Array(Type.String({minLength: 1}), {description: "按目标顺序列出的参考图资源 ID"})), role_overrides: Type.Optional(Type.Object({}, {description: "参考图资源 ID → 角色（product/detail/layout/scene_style/general）"})) }),
    },
    {
        name: "canvas.group_create",
        description: "新建显式排版框；组员及其连线保持独立。同标题、同 Agent 的排版框复用（不增加成员）；不转换旧分组，不收已入组节点，不允许嵌套。anchor 默认 auto（画布右侧）；也可指定 right_of_selection 或 {x,y}。默认跳过受保护节点。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ title: Type.String({minLength: 1, maxLength: 200}), node_ids: Type.Optional(Type.Array(Type.String({minLength: 1, maxLength: 500}), {maxItems: 500, uniqueItems: true})), plan_section: Type.Optional(Type.String({minLength: 1, maxLength: 200})), include_user_nodes: Type.Optional(Type.Boolean({description: "仅当用户明确点名 node_ids 中的用户节点时设为 true；只有无需确认档且本轮有相应的用户节点引用可免确认。"})), anchor: Type.Optional(Type.Union([Type.Object({ x: Type.Number({minimum: -10000000, maximum: 10000000}), y: Type.Number({minimum: -10000000, maximum: 10000000}) }, {additionalProperties: false}), Type.Union([Type.Literal("right_of_selection"), Type.Literal("auto")])])) }),
    },
    {
        name: "canvas.group_arrange",
        description: "按 grid（最多三列）、row 或 column 排列新建排版框的自有组员。sort_by 默认 current（先上后下、先左后右）；受保护组员原地保留，旧分组和会话框不支持。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ group_id: Type.String({minLength: 1, maxLength: 500}), layout: Type.Union([Type.Literal("grid"), Type.Literal("row"), Type.Literal("column")]), sort_by: Type.Optional(Type.Union([Type.Literal("plan_order"), Type.Literal("title"), Type.Literal("current")])), gap: Type.Optional(Type.Number({minimum: 0, maximum: 10000000})) }),
    },
    {
        name: "canvas.arrange_groups",
        description: "按传入顺序并排或竖排显式排版框；成员随框平移。跳过旧框、受保护框及含受保护成员的框。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ group_ids: Type.Array(Type.String({minLength: 1, maxLength: 500}), {maxItems: 500, uniqueItems: true}), direction: Type.Union([Type.Literal("row"), Type.Literal("column")]), gap: Type.Optional(Type.Number({minimum: 0, maximum: 10000000})) }),
    },
    {
        name: "canvas.align",
        description: "将可移动卡片向首个可移动目标的指定边缘/中心对齐，默认跳过受保护节点；整组平移请用 arrange_groups。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_ids: Type.Array(Type.String({minLength: 1, maxLength: 500}), {maxItems: 500, uniqueItems: true}), edge: Type.Union([Type.Literal("left"), Type.Literal("center_x"), Type.Literal("right"), Type.Literal("top"), Type.Literal("center_y"), Type.Literal("bottom")]), include_user_nodes: Type.Optional(Type.Boolean({description: "仅当用户明确点名 node_ids 中的用户节点时设为 true；只有无需确认档且本轮有相应的用户节点引用可免确认。"})) }),
    },
    {
        name: "canvas.distribute",
        description: "沿指定轴在最外侧卡片之间等距分布（至少三张）；指定 gap 时从首张开始定距排列（至少两张）。默认跳过受保护节点。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_ids: Type.Array(Type.String({minLength: 1, maxLength: 500}), {maxItems: 500, uniqueItems: true}), axis: Type.Union([Type.Literal("x"), Type.Literal("y")]), gap: Type.Optional(Type.Number({minimum: 0, maximum: 10000000})), include_user_nodes: Type.Optional(Type.Boolean({description: "仅当用户明确点名 node_ids 中的用户节点时设为 true；只有无需确认档且本轮有相应的用户节点引用可免确认。"})) }),
    },
    {
        name: "canvas.search_projects",
        description: "跨项目搜索（只读，票 F/ADR-0026）：按关键词搜所有项目画布节点的标题/prompt/正文与项目名，返回按项目分组命中（node_id/kind/field/snippet）；不搜 agent 会话内容，不建索引",
        parameters: Type.Object({ query: Type.String({description: "搜索关键词（大小写不敏感）", minLength: 1, maxLength: 200}), limit: Type.Optional(Type.Integer({description: "命中总数上限（默认 50）", minimum: 1, maximum: 200})) }),
    },
    {
        name: "canvas.view",
        description: "整页/选区节点媒体导出为一张图（contact sheet，以 MCP image 内容块返回，文本块仅含元数据）喂模型（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s；超时时先调低 max_px 或改用 selection 缩小批量，再重试",
        parameters: Type.Object({ scope: Type.Union([Type.Literal("page"), Type.Literal("selection")], {description: "page=整页（所有带媒体节点）；selection=指定 node_ids"}), node_ids: Type.Optional(Type.Array(Type.String(), {description: "节点 ID 列表"})), max_px: Type.Optional(Type.Integer({description: "预览最长边像素，默认 1024，上限 1568；PNG 超过 1 MiB 时继续等比缩小，不改媒体原件（#352）", minimum: 64, maximum: 1568})) }),
    },
    {
        name: "canvas.view_media",
        description: "查看单节点媒体：图像下缩放，视频抽帧拼图（contact sheet），以 image 内容块返回，文本仅含元数据（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s",
        parameters: Type.Object({ node_id: Type.String(), max_px: Type.Optional(Type.Integer({description: "预览最长边像素，默认 1024，上限 1568；PNG 超过 1 MiB 时继续等比缩小，不改媒体原件（#352）", minimum: 64, maximum: 1568})), frames: Type.Optional(Type.Integer({description: "视频抽帧数 1..16，默认 4", minimum: 1, maximum: 16})) }),
    },
    {
        name: "canvas.edit_crop",
        description: "裁剪节点图像选区并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), rect: Type.Object({ x: Type.Number(), y: Type.Number(), width: Type.Number(), height: Type.Number() }, {description: "选区（原图像素坐标，x/y 左上角 + width/height）"}) }),
    },
    {
        name: "canvas.batch_prompt_edit",
        description: "批量编辑文本或图像/视频节点的提示词，不触发生图。先以 apply:false + edits 预览，再以 apply:true + diff_id 应用。浏览器整批确认（含用户改过的提示词），取消零写入；每个预览只可尝试应用一次。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ apply: Type.Boolean(), diff_id: Type.Optional(Type.String({minLength: 1})), edits: Type.Optional(Type.Array(Type.Object({ node_id: Type.String({minLength: 1}), prompt: Type.Optional(Type.String()), append: Type.Optional(Type.String()), find: Type.Optional(Type.String({minLength: 1})), replace: Type.Optional(Type.String()) }), {minItems: 1, maxItems: 50})) }),
    },
    {
        name: "canvas.batch_edit",
        description: "批量生成：把同一句 prompt 写入最多 10 个选中节点并逐节点触发一次生成（消耗外部生成任务，区别于 edit_* 的本地处理；结果放各自旁边 = 各节点自身输出）。总是请求浏览器整批确认提示词覆盖和生图费用（阻塞最长 90s），每 agent 会话按张数计入生成上限；返回各节点的 task_id，用 generate.status 轮询 / generate.wait 等待完成。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_ids: Type.Array(Type.String(), {description: "节点 ID 列表（1..10）", minItems: 1, maxItems: 10}), prompt: Type.String({description: "批量 prompt，写入每个节点", minLength: 1}) }),
    },
    {
        name: "canvas.edit_rotate",
        description: "旋转节点图像（90/180/270 逆时针）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), angle: Type.Union([Type.Literal(90), Type.Literal(180), Type.Literal(270)], {description: "逆时针旋转角度，90/180/270 之一"}) }),
    },
    {
        name: "canvas.edit_flip",
        description: "镜像节点图像（horizontal 左右 / vertical 上下）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), direction: Type.Union([Type.Literal("horizontal"), Type.Literal("vertical")], {description: "horizontal=左右镜像，vertical=上下镜像"}) }),
    },
    {
        name: "canvas.edit_outpaint",
        description: "复刻站内红底扩图链：扩大画布用高饱和红（#ff0044）填充、原图按 offset 贴回，覆写节点图（本地 PIL，不消耗外部生成；外区红底供 generate.trigger 重绘填充，不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), outpaint_size: Type.Object({ width: Type.Integer(), height: Type.Integer() }, {description: "目标画布尺寸（像素；须大于原图像素）"}), offset: Type.Optional(Type.Object({ x: Type.Optional(Type.Integer()), y: Type.Optional(Type.Integer()) }, {description: "原图在新画布中的位置（像素，默认 0,0 左上角）"})) }),
    },
    {
        name: "canvas.edit_erase",
        description: "按 prompt 擦除节点图选区内容：裁出选区送 bailian 局部编辑模型（qwen-image-edit-max-2026-01-16）重绘后合成回节点图（消耗一次外部模型调用；不向模型发 mask——ADR 0007；写操作：需画布确认门批准，阻塞最长 90s，原资源保留在资源库可恢复）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String(), rect: Type.Object({ x: Type.Number(), y: Type.Number(), width: Type.Number(), height: Type.Number() }, {description: "选区（原图像素坐标，x/y 左上角 + width/height）"}), prompt: Type.String({description: "描述要擦除什么、如何填充该区域", minLength: 1}) }),
    },
    {
        name: "generate.trigger",
        description: "按节点保存的 runSettings 和画布参考图生图，返回 task_id。参考图必须已归档到当前项目。按本次请求图片张数确认和计入会话额度（阻塞最长 90s，autonomous 自动放行）；结果写入项目历史。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ node_id: Type.String() }),
    },
    {
        name: "generate.retry",
        description: "在同一项目和 Agent 作用域内，按 task_id 或 node_id 重试失败且无产物的生图（恰传一个）；默认使用服务端快照。每次均须用户重新确认并计入生成额度，最多两次；可指定已经批准的 model。不支持重放已撤回或自动归档的历史参考图。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ task_id: Type.Optional(Type.String()), node_id: Type.Optional(Type.String()), model: Type.Optional(Type.String()), use_snapshot: Type.Optional(Type.Boolean()) }),
    },
    {
        name: "generate.preflight",
        description: "只读预检生图：提供 node_ids 或 group_id（恰一），查看最终提示词、参考图、模型、张数与阻断原因，不归档参考图",
        parameters: Type.Object({ node_ids: Type.Optional(Type.Array(Type.String(), {minItems: 1, maxItems: 200})), group_id: Type.Optional(Type.String()) }),
    },
    {
        name: "generate.request_batch",
        description: "请求整批生图批准；立即返回 batch_id，不等待浏览器确认（90 秒有效）。同一 preflight_id 只提交一次。MCP 请沿用同一个 Mcp-Session-Id 并选用已存在的 Agent 节点。仅在已批准的模型范围内换模型，自动重试最多两次；额度用尽会暂停剩余项",
        parameters: Type.Object({ preflight_id: Type.String(), fallback_models: Type.Optional(Type.Array(Type.String(), {maxItems: 20})), max_retries: Type.Optional(Type.Integer({minimum: 0})) }),
    },
    {
        name: "generate.batch_status",
        description: "查询批次项状态、批准张数和已启动张数。可等待下一次变化，wait_seconds 0..600（默认 0）",
        parameters: Type.Object({ batch_id: Type.String(), wait_seconds: Type.Optional(Type.Number({minimum: 0, maximum: 600})) }),
    },
    {
        name: "generate.stop_batch",
        description: "取消未批准的批次或阻止已批准批次启动更多任务；已经启动的任务和结果保留",
        parameters: Type.Object({ batch_id: Type.String() }),
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
        description: "单独导入项目媒体：file_path 为服务器本机路径；或使用 attachment_id 将已发送的会话图片在浏览器批准后明确归档。仅传其中一项。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
        parameters: Type.Object({ file_path: Type.Optional(Type.String()), attachment_id: Type.Optional(Type.String({description: "已发送到当前 Agent 会话的附件 ID，不是文件路径或 asset_id", minLength: 32, maxLength: 32})) }),
    },
    {
        name: "media.list",
        description: "列出项目媒体资源",
        parameters: Type.Object({}),
    },
    {
        name: "media.delete",
        description: "删除媒体资源（破坏性：需画布确认门批准，阻塞最长 90s）。仅外部 MCP：可在 URL 查询参数中提供 turn_id（非 JSON 工具参数）；同一轮的写调用可共用该 ID，turn_id 不得复用到其他轮次。",
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
