import type { ExtensionAPI } from "pi-stable";
import { CANVAS_TOOL_DEFS } from "./canvas-tools.generated.ts";

// Base URL for FastAPI backend (injected via environment or config)
// #279：兜底与服务默认监听端口 3000 一致（见 main.py）；实际由 agent_host 注入
// FIREFLY_AGENT_API_BASE 覆盖，不再出现 8000。
const API_BASE = process.env.FIREFLY_AGENT_API_BASE || "http://127.0.0.1:3000";
const PROJECT_ID = process.env.FIREFLY_AGENT_PROJECT_ID || "";
const NODE_ID = process.env.FIREFLY_AGENT_NODE_ID || "";

async function callApi(path: string, method: string, body?: any) {
    const url = `${API_BASE}${path}`;
    const options: RequestInit = {
        method,
        headers: {
            "Content-Type": "application/json",
            "X-Agent-Project": PROJECT_ID,
        },
    };
    if (body !== undefined) {
        options.body = JSON.stringify(body);
    }
    try {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }
        return data;
    } catch (e: any) {
        throw new Error(`API call failed: ${e.message}`);
    }
}

// ── #284 媒体 URL 归一化（纯函数；行为测试 tests/canvas_module_units/test-agent-canvas-tools.mjs）──
// 工具返回值带「可服务绝对 URL」：生成目录 OUTPUT_OUTPUT_DIR=ASSETS_DIR/output（main.py:1090），
// StaticFiles 实际挂载 /output 与 /assets（main.py:1180-1182）→ 可服务前缀 /assets/output/<file>。
// 历史的 /media/<f>、裸文件名会 404；这里统一改写成带 API_BASE 主机的绝对 URL，
// 模型可直接放进 MEDIA: 行。非媒体字符串原样返回（保守，不污染 task_id/status/base64/提示词）。
// 注意：下面两个纯函数故意不写 TS 类型标注（保持可被 Node 直接求值的行为测试所抽取），
// 仅依赖模块级 API_BASE。测试按 SENTINEL 注释切片后在 new Function 里求值（当前 realm，避免 vm 跨 realm 原型差异）。
const MEDIA_NAME_RE = /^(generated_image|generated_video|generated_audio|frame_|thumbnail_|online_|video_|audio_)/;

function normalizeMediaUrl(raw) {
    if (typeof raw !== "string") return raw;                                   // null/undefined/数字 原样
    const file = raw.trim();
    if (!file) return file;
    if (/^https?:\/\//i.test(file)) return file;                            // 已是绝对 URL，原样
    if (/^\/(api\/|assets\/|output\/)/i.test(file)) return API_BASE + file; // 已可服务的相对路径 → 补主机
    const toAbsolute = (name) => `${API_BASE}/assets/output/${name}`;
    if (/^\/media\//i.test(file)) return toAbsolute(file.replace(/^\/media\//i, ""));
    const served = /[\\/](?:assets[\\/])?output[\\/]([^\\/]+)$/.exec(file);
    if (served && (/^([A-Za-z]:[\\/]|\/|\.)/.test(file) || /assets[\\/]output/i.test(file))) {
        return toAbsolute(served[1]);                                       // 绝对/磁盘路径里的 output/<file>
    }
    if (file.includes("/")) {
        const name = file.split(/[\\/]/).filter(Boolean).pop() || file;
        return MEDIA_NAME_RE.test(name) ? toAbsolute(name) : file;          // 相对路径：仅改写已知媒体前缀
    }
    return MEDIA_NAME_RE.test(file) ? toAbsolute(file) : file;              // 裸文件名：仅改写已知媒体前缀
}

// 递归改写工具返回值中的媒体 URL（只处理字符串；数字/布尔/null 原样；对象/数组深遍历）。
function normalizeMediaPayload(value) {
    if (typeof value === "string") return normalizeMediaUrl(value);
    if (Array.isArray(value)) return value.map(normalizeMediaPayload);
    if (value && typeof value === "object") {
        const out = {};
        for (const key of Object.keys(value)) out[key] = normalizeMediaPayload(value[key]);
        return out;
    }
    return value;
}
// ── end #284 媒体 URL 归一化 ──

// #352：视图类返回值 image_data_url → image 内容块，文本仅元数据（base64 文本不再撑爆上下文）。
// 注：刻意不写 TS 类型标注，保证 Node 能直接求值（同 #284 helper 约定）。
function buildViewToolResult(data) {
    const dataUrl = String(data?.image_data_url || "");
    const comma = dataUrl.indexOf(",");
    if (comma < 0) {
        return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }], details: {} };
    }
    const mime = (dataUrl.startsWith("data:") ? dataUrl.slice(5, comma).split(";")[0] : "") || "image/png";
    const meta = { ...(data || {}) };
    delete meta.image_data_url;
    return {
        content: [
            { type: "image", data: dataUrl.slice(comma + 1), mimeType: mime },
            { type: "text", text: JSON.stringify(normalizeMediaPayload(meta), null, 2) },
        ],
        details: {},
    };
}

// ── #356 结构说明 ──
// 工具 name/description/参数 schema 全部来自 CANVAS_TOOL_DEFS（canvas-tools.generated.ts），
// 由 tools/gen_canvas_tools.py 从 app/services/agent_tools_manifest.py 生成——与 MCP 端
// （app/routers/api_mcp.py）同一来源；本文件只剩 execute 执行器（REST 孪生调用）。
// 每个清单工具必须在 EXECUTORS 里有同名键，否则加载即 throw；
// tests/test_tool_surface_consistency.py 双向钉住名字集合。
// D11（#356）：客户端不做参数默认值——create_node 不发 id、不默认 x/y（服务端 frame
// 自动落位，agent_node_id 随 body 透传）；connect 不发 edge id、不默认 kind（服务端缺省
// flow）；search_projects 透传 limit（D13）。

function textResult(payload: any, normalize = false) {
    const body = normalize ? normalizeMediaPayload(payload) : payload;
    return { content: [{ type: "text", text: JSON.stringify(body, null, 2) }], details: {} };
}

// #357：确认门移到服务端（统一执行入口 _server_confirm_gate）——本扩展不再预确认，
// 只在写操作 URL 上带 ?agent_node_id=（服务端据此把确认事件投影到本节点会话）。
// 被拒/超时 → 服务端 409，callApi 抛错，pi 侧看到拒绝/超时文本。
function gateQuery() {
    return NODE_ID ? `?agent_node_id=${encodeURIComponent(NODE_ID)}` : "";
}

const EXECUTORS: Record<string, (toolCallId: string, params: any) => Promise<any>> = {
    "canvas.list_nodes": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes`, "GET")),

    "canvas.get_node": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "GET")),

    "canvas.create_node": async (_toolCallId, params) => {
        // #356 D11：只发显式字段；id 与缺位落位归服务端（agent_node_id 透传启用 frame 网格）。
        const node: any = { type: params.type };
        if (params.title !== undefined) node.title = params.title;
        if (params.x !== undefined) node.x = params.x;
        if (params.y !== undefined) node.y = params.y;
        if (params.text !== undefined) node.text = params.text;
        if (params.prompt !== undefined) node.prompt = params.prompt;
        const body: any = { node };
        if (NODE_ID) body.agent_node_id = NODE_ID;
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes`, "POST", body));
    },

    "canvas.update_node": async (_toolCallId, params) => {
        const updates: any = {};
        if (params.title !== undefined) updates.title = params.title;
        if (params.text !== undefined) updates.text = params.text;
        if (params.prompt !== undefined) updates.prompt = params.prompt;
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "PATCH", updates));
    },

    "canvas.delete_node": async (_toolCallId, params) => {
        // #357：确认在服务端门里发生（阻塞最长 90s），这里只带身份参数。
        await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}${gateQuery()}`, "DELETE");
        return { content: [{ type: "text", text: `节点 ${params.node_id} 已删除。` }], details: {} };
    },

    "canvas.connect": async (_toolCallId, params) => {
        // #356 D12/D11：edge id 服务端铸造；kind 缺省归服务端 core（flow）。
        const edge: any = { from: params.from_node, to: params.to_node };
        if (params.kind !== undefined) edge.kind = params.kind;
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edges`, "POST", { edge }));
    },

    "canvas.disconnect": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edges/${params.from_node}/${params.to_node}${gateQuery()}`, "DELETE")),

    "canvas.search_projects": async (_toolCallId, params) => {
        // D13（#356）：limit 透传（此前声明了但从不拼进查询串，永远 50）。
        let url = `/api/search/projects?q=${encodeURIComponent(params.query)}`;
        if (params.limit !== undefined) url += `&limit=${encodeURIComponent(String(params.limit))}`;
        return textResult(await callApi(url, "GET"));
    },

    "canvas.view": async (_toolCallId, params) => {
        const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/view`, "POST", {
            scope: params.scope,
            node_ids: params.node_ids,
            max_px: params.max_px,
        });
        return buildViewToolResult(data);
    },

    "canvas.view_media": async (_toolCallId, params) => {
        const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/view_media`, "POST", {
            node_id: params.node_id,
            max_px: params.max_px,
            frames: params.frames,
        });
        return buildViewToolResult(data);
    },

    "canvas.edit_crop": async (_toolCallId, params) => {
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_crop${gateQuery()}`, "POST", {
            node_id: params.node_id,
            rect: params.rect,
        }));
    },

    "canvas.batch_edit": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/batch_edit`, "POST", {
            node_ids: params.node_ids,
            prompt: params.prompt,
        })),

    "canvas.edit_rotate": async (_toolCallId, params) => {
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_rotate${gateQuery()}`, "POST", {
            node_id: params.node_id,
            angle: params.angle,
        }));
    },

    "canvas.edit_flip": async (_toolCallId, params) => {
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_flip${gateQuery()}`, "POST", {
            node_id: params.node_id,
            direction: params.direction,
        }));
    },

    "canvas.edit_outpaint": async (_toolCallId, params) => {
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_outpaint${gateQuery()}`, "POST", {
            node_id: params.node_id,
            outpaint_size: params.outpaint_size,
            offset: params.offset,
        }));
    },

    "canvas.edit_erase": async (_toolCallId, params) => {
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_erase${gateQuery()}`, "POST", {
            node_id: params.node_id,
            rect: params.rect,
            prompt: params.prompt,
        }));
    },

    "generate.trigger": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/generate/trigger`, "POST", { node_id: params.node_id })),

    "generate.status": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/generate/tasks/${params.task_id}`, "GET"), true),

    "generate.wait": async (_toolCallId, params) => {
        // #243: timeout 走 Query 参数（服务端 wait_for_task 是 Query 参数
        // timeout:1..600 default 120）；此前发在 GET body 被服务端忽略。
        const timeout = Math.max(1, Math.min(600, Math.round(params.timeout_seconds ?? 120)));
        return textResult(await callApi(`/api/projects/${PROJECT_ID}/generate/tasks/${params.task_id}/wait?timeout=${timeout}`, "GET"), true);
    },

    "media.upload": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/media/upload`, "POST", { file_path: params.file_path }), true),

    "media.list": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/media/list`, "GET"), true),

    "media.delete": async (_toolCallId, params) => {
        // #357：确认在服务端门里发生。
        await callApi(`/api/projects/${PROJECT_ID}/media/${params.asset_id}${gateQuery()}`, "DELETE");
        return { content: [{ type: "text", text: `媒体文件 ${params.asset_id} 已删除。` }], details: {} };
    },

    "viewport.focus": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/viewport/focus`, "POST", { node_id: params.node_id })),

    "viewport.zoom": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/viewport/zoom`, "POST", { level: params.level })),

    "viewport.state": async (_toolCallId, params) =>
        textResult(await callApi(`/api/projects/${PROJECT_ID}/viewport/state`, "GET")),
};

export default function (pi: ExtensionAPI) {
    for (const def of CANVAS_TOOL_DEFS) {
        const execute = EXECUTORS[def.name];
        if (!execute) {
            throw new Error(`[canvas-tools] EXECUTORS 缺少工具 ${def.name}（清单与执行器漂移）`);
        }
        pi.registerTool({ name: def.name, description: def.description, parameters: def.parameters, execute });
    }
}
