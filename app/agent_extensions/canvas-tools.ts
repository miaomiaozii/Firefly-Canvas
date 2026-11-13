import type { ExtensionAPI } from "pi-stable";
import { Type } from "typebox";

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

export default function (pi: ExtensionAPI) {
    // Query tools
    pi.registerTool({
        name: "canvas.list_nodes",
        description: "List all nodes on the canvas with summary info (id, type, title, position, connections, status)",
        parameters: Type.Object({}),
        async execute() {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes`, "GET");
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.get_node",
        description: "Get detailed information about a specific node",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to query" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "GET");
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    // #243: canvas.get_selection 已摘除——它是恒空的假工具（选择状态是纯客户端
    // 状态，服务端没有数据来源），ADR 0022 已知缺口「摘除或实装」选摘除。

    // Structure tools
    pi.registerTool({
        name: "canvas.create_node",
        description: "Create a new node on the canvas",
        parameters: Type.Object({
            type: Type.String({ description: "Node type: smart-prompt, smart-image, smart-video, smart-agent, conversation" }),
            title: Type.Optional(Type.String({ description: "Node title" })),
            x: Type.Optional(Type.Number({ description: "X position" })),
            y: Type.Optional(Type.Number({ description: "Y position" })),
            text: Type.Optional(Type.String({ description: "Text content (for prompt nodes)" })),
            prompt: Type.Optional(Type.String({ description: "Prompt content (for image/video nodes)" })),
        }),
        async execute(toolCallId, params) {
            const node = {
                id: `node-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                type: params.type,
                title: params.title || "",
                x: params.x ?? 0,
                y: params.y ?? 0,
            };
            if (params.text !== undefined) node.text = params.text;
            if (params.prompt !== undefined) node.prompt = params.prompt;

            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes`, "POST", { node });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.update_node",
        description: "Update an existing node's properties",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to update" }),
            title: Type.Optional(Type.String({ description: "New title" })),
            text: Type.Optional(Type.String({ description: "New text content" })),
            prompt: Type.Optional(Type.String({ description: "New prompt content" })),
        }),
        async execute(toolCallId, params) {
            const updates: any = {};
            if (params.title !== undefined) updates.title = params.title;
            if (params.text !== undefined) updates.text = params.text;
            if (params.prompt !== undefined) updates.prompt = params.prompt;

            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "PATCH", updates);
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.delete_node",
        description: "Delete a node from the canvas (requires user confirmation)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to delete" }),
        }),
        async execute(toolCallId, params) {
            // Hard confirmation gate: ask the user via the backend confirm endpoint
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.delete_node",
                    params: { node_id: params.node_id },
                    message: `删除节点 ${params.node_id}？此操作不可撤销。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{
                        type: "text",
                        text: `删除已取消：${reason}。节点 ${params.node_id} 未被删除。`,
                    }],
                    details: { approved: false, reason: confirmData.reason },
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "DELETE");
            return {
                content: [{ type: "text", text: `节点 ${params.node_id} 已删除。` }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.connect",
        description: "Create a connection between two nodes",
        parameters: Type.Object({
            from_node: Type.String({ description: "Source node ID" }),
            to_node: Type.String({ description: "Target node ID" }),
            kind: Type.Optional(Type.String({ description: "Connection kind: input, flow" })),
        }),
        async execute(toolCallId, params) {
            const edge = {
                id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                from: params.from_node,
                to: params.to_node,
                kind: params.kind || "flow",
            };
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edges`, "POST", { edge });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.disconnect",
        description: "Remove a connection between two nodes",
        parameters: Type.Object({
            from_node: Type.String({ description: "Source node ID" }),
            to_node: Type.String({ description: "Target node ID" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/canvas/edges/${params.from_node}/${params.to_node}`,
                "DELETE"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.search_projects",
        description: "跨项目搜索（只读）：按关键词搜所有项目画布节点的标题/prompt/正文与项目名，返回按项目分组命中；不搜 agent 会话内容",
        parameters: Type.Object({
            query: Type.String({ description: "搜索关键词（大小写不敏感）" }),
            limit: Type.Number({ description: "命中总数上限（1..200，默认 50）", optional: true }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/search/projects?q=${encodeURIComponent(params.query)}`,
                "GET"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    // #266 只读视图工具（ComfyArt §59.4 语义；慢操作独立 90s 超时契约，服务端施加）
    pi.registerTool({
        name: "canvas.view",
        description:
            "Export canvas nodes' media as one contact-sheet image (base64 PNG) to feed the model. scope: page (all media nodes) or selection (node_ids). Read-only. Slow op: server-side 90s timeout — set client request timeout >= 90s.",
        parameters: Type.Object({
            scope: Type.Union([Type.Literal("page"), Type.Literal("selection")], { description: "page=整页；selection=指定节点" }),
            node_ids: Type.Optional(Type.Array(Type.String(), { description: "Node IDs for scope=selection" })),
            max_px: Type.Optional(Type.Number({ description: "Longest side in px, default 1024, cap 2048" })),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/view`, "POST", {
                scope: params.scope,
                node_ids: params.node_ids,
                max_px: params.max_px,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.view_media",
        description:
            "View one node's media: image downscaled, video as frame contact sheet. Read-only. Slow op: server-side 90s timeout — set client request timeout >= 90s.",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to view" }),
            max_px: Type.Optional(Type.Number({ description: "Longest side in px, default 1024, cap 2048" })),
            frames: Type.Optional(Type.Number({ description: "Video frame count 1..16, default 4" })),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/view_media`, "POST", {
                node_id: params.node_id,
                max_px: params.max_px,
                frames: params.frames,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(normalizeMediaPayload(data), null, 2) }],
                details: {},
            };
        },
    });

    // #266 edit_* 首批（包装 canvas-image-editor.js 已有能力）：写操作先走确认门（#242 语义）
    pi.registerTool({
        name: "canvas.edit_crop",
        description:
            "Crop a node image selection and overwrite the node image (local PIL, no external generation; requires user confirmation; the original asset stays in the library)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to edit" }),
            rect: Type.Object({
                x: Type.Number({ description: "Selection left (image px)" }),
                y: Type.Number({ description: "Selection top (image px)" }),
                width: Type.Number({ description: "Selection width (image px)" }),
                height: Type.Number({ description: "Selection height (image px)" }),
            }, { description: "Selection in source-image pixel coordinates" }),
        }),
        async execute(toolCallId, params) {
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.edit_crop",
                    params: { node_id: params.node_id, rect: params.rect },
                    message: `裁剪节点 ${params.node_id} 的图像并覆写节点图？原资源保留在资源库。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{ type: "text", text: JSON.stringify({ approved: false, reason }, null, 2) }],
                    details: {},
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_crop`, "POST", {
                node_id: params.node_id,
                rect: params.rect,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    // #266 canvas.batch_edit（§59.3）：N 节点 + 一句 prompt = N 次生成（调既有生成通路，消耗生成任务）
    pi.registerTool({
        name: "canvas.batch_edit",
        description:
            "Batch generation: write one prompt to up to 10 selected nodes and trigger one generation per node (consumes external generation, unlike local edit_*; results stay on each node). Returns per-node task_ids — poll generate.status or wait with generate.wait",
        parameters: Type.Object({
            node_ids: Type.Array(Type.String(), { description: "Node IDs to generate on (1..10)" }),
            prompt: Type.String({ description: "The batch prompt, written to every node" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/batch_edit`, "POST", {
                node_ids: params.node_ids,
                prompt: params.prompt,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    // #266b edit_* 再扩两项（包装 canvas-image-editor.js 已有能力：rotate 既有能力，flip 同族本地变换）：写操作先走确认门（#242 语义）
    pi.registerTool({
        name: "canvas.edit_rotate",
        description:
            "Rotate a node image (90/180/270 degrees counter-clockwise) and overwrite the node image (local PIL, no external generation; requires user confirmation; the original asset stays in the library)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to edit" }),
            angle: Type.Union([Type.Literal(90), Type.Literal(180), Type.Literal(270)], { description: "Counter-clockwise rotation in degrees: 90, 180 or 270" }),
        }),
        async execute(toolCallId, params) {
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.edit_rotate",
                    params: { node_id: params.node_id, angle: params.angle },
                    message: `旋转节点 ${params.node_id} 的图像并覆写节点图？原资源保留在资源库。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{ type: "text", text: JSON.stringify({ approved: false, reason }, null, 2) }],
                    details: {},
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_rotate`, "POST", {
                node_id: params.node_id,
                angle: params.angle,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.edit_flip",
        description:
            "Mirror a node image (horizontal left-right / vertical top-bottom) and overwrite the node image (local PIL, no external generation; requires user confirmation; the original asset stays in the library)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to edit" }),
            direction: Type.Union([Type.Literal("horizontal"), Type.Literal("vertical")], { description: "horizontal = left-right mirror, vertical = top-bottom mirror" }),
        }),
        async execute(toolCallId, params) {
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.edit_flip",
                    params: { node_id: params.node_id, direction: params.direction },
                    message: `镜像节点 ${params.node_id} 的图像并覆写节点图？原资源保留在资源库。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{ type: "text", text: JSON.stringify({ approved: false, reason }, null, 2) }],
                    details: {},
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_flip`, "POST", {
                node_id: params.node_id,
                direction: params.direction,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.edit_outpaint",
        description:
            "Red-mask outpaint: enlarge the canvas, fill the outer region with saturated red (#ff0044) and paste the original image at offset, then overwrite the node image (local PIL, no external generation; the red outer region is later filled by generate.trigger — no mask is sent to any model, ADR 0007; requires user confirmation; the original asset stays in the library)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to edit" }),
            outpaint_size: Type.Object({
                width: Type.Number({ description: "Target canvas width in pixels (must exceed the original image width)" }),
                height: Type.Number({ description: "Target canvas height in pixels (must exceed the original image height)" }),
            }, { description: "Target canvas size in pixels" }),
            offset: Type.Object({
                x: Type.Number({ description: "Original image X position on the new canvas (default 0)" }),
                y: Type.Number({ description: "Original image Y position on the new canvas (default 0)" }),
            }, { description: "Position of the original image on the new canvas (default 0,0 top-left)" }),
        }),
        async execute(toolCallId, params) {
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.edit_outpaint",
                    params: {
                        node_id: params.node_id,
                        outpaint_size: params.outpaint_size,
                        offset: params.offset,
                    },
                    message: `红底扩图节点 ${params.node_id}（目标画布 ${params.outpaint_size?.width}x${params.outpaint_size?.height}，外区填 #ff0044）并覆写节点图？原资源保留在资源库。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{ type: "text", text: JSON.stringify({ approved: false, reason }, null, 2) }],
                    details: {},
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_outpaint`, "POST", {
                node_id: params.node_id,
                outpaint_size: params.outpaint_size,
                offset: params.offset,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "canvas.edit_erase",
        description:
            "Erase content inside a node-image selection (rect x/y/width/height in original image pixels) per prompt: the rect is cropped, sent to the bailian local-edit model (qwen-image-edit-max-2026-01-16), and the result is composited back into the node image (consumes one external model call; no mask is sent to any model — ADR 0007; requires user confirmation; the original asset stays in the library)",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to edit" }),
            rect: Type.Object({
                x: Type.Number({ description: "Selection left edge in original image pixels" }),
                y: Type.Number({ description: "Selection top edge in original image pixels" }),
                width: Type.Number({ description: "Selection width in original image pixels" }),
                height: Type.Number({ description: "Selection height in original image pixels" }),
            }, { description: "Selection in original image pixels (x/y top-left corner + width/height)" }),
            prompt: Type.String({ description: "Describe what to erase and how to fill the region" }),
        }),
        async execute(toolCallId, params) {
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "canvas.edit_erase",
                    params: {
                        node_id: params.node_id,
                        rect: params.rect,
                        prompt: params.prompt,
                    },
                    message: `擦除节点 ${params.node_id} 选区（${params.rect?.x},${params.rect?.y} ${params.rect?.width}x${params.rect?.height}，"${params.prompt}"）并覆写节点图？将消耗一次外部模型调用（qwen-image-edit），原资源保留在资源库。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{ type: "text", text: JSON.stringify({ approved: false, reason }, null, 2) }],
                    details: {},
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/edit_erase`, "POST", {
                node_id: params.node_id,
                rect: params.rect,
                prompt: params.prompt,
            });
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    // Generate tools
    pi.registerTool({
        name: "generate.trigger",
        description: "Trigger generation on a node, returns task ID",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to generate on" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/generate/trigger`,
                "POST",
                { node_id: params.node_id }
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "generate.status",
        description: "Query the status of a generation task",
        parameters: Type.Object({
            task_id: Type.String({ description: "The task ID to query" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/generate/tasks/${params.task_id}`,
                "GET"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(normalizeMediaPayload(data), null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "generate.wait",
        description: "Wait for a generation task to complete (with timeout)",
        parameters: Type.Object({
            task_id: Type.String({ description: "The task ID to wait for" }),
            timeout_seconds: Type.Optional(Type.Number({ description: "Timeout in seconds (default 120)" })),
        }),
        async execute(toolCallId, params) {
            // #243: timeout 走 Query 参数（服务端 wait_for_task 是 Query 参数
            // timeout:1..600 default 120）；此前发在 GET body 被服务端忽略。
            const timeout = Math.max(1, Math.min(600, Math.round(params.timeout_seconds ?? 120)));
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/generate/tasks/${params.task_id}/wait?timeout=${timeout}`,
                "GET"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(normalizeMediaPayload(data), null, 2) }],
                details: {},
            };
        },
    });

    // Media tools
    pi.registerTool({
        name: "media.upload",
        description: "Upload a media file to the project library, returns asset ID",
        parameters: Type.Object({
            file_path: Type.String({ description: "Path to the media file to upload" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/media/upload`,
                "POST",
                { file_path: params.file_path }
            );
            return {
                content: [{ type: "text", text: JSON.stringify(normalizeMediaPayload(data), null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "media.list",
        description: "List all media assets in the project",
        parameters: Type.Object({}),
        async execute() {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/media/list`,
                "GET"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(normalizeMediaPayload(data), null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "media.delete",
        description: "Delete a media file from the project (requires user confirmation)",
        parameters: Type.Object({
            asset_id: Type.String({ description: "The asset ID to delete" }),
        }),
        async execute(toolCallId, params) {
            // Hard confirmation gate: ask the user via the backend confirm endpoint
            const confirmData = await callApi(
                `/api/projects/${PROJECT_ID}/agent/confirm`,
                "POST",
                {
                    node_id: NODE_ID,
                    tool: "media.delete",
                    params: { asset_id: params.asset_id },
                    message: `删除媒体文件 ${params.asset_id}？此操作不可撤销。`,
                }
            );
            if (!confirmData.approved) {
                const reason = confirmData.reason === "timeout" ? "用户未响应（超时）" : "用户取消";
                return {
                    content: [{
                        type: "text",
                        text: `删除已取消：${reason}。媒体文件 ${params.asset_id} 未被删除。`,
                    }],
                    details: { approved: false, reason: confirmData.reason },
                };
            }
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/media/${params.asset_id}`,
                "DELETE"
            );
            return {
                content: [{ type: "text", text: `媒体文件 ${params.asset_id} 已删除。` }],
                details: {},
            };
        },
    });

    // Viewport tools
    pi.registerTool({
        name: "viewport.focus",
        description: "Scroll canvas to focus on a node",
        parameters: Type.Object({
            node_id: Type.String({ description: "The node ID to focus on" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/viewport/focus`,
                "POST",
                { node_id: params.node_id }
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "viewport.zoom",
        description: "Set the canvas zoom level (0.1 to 8.0)",
        parameters: Type.Object({
            level: Type.Number({ description: "Zoom level (0.1 to 8.0)" }),
        }),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/viewport/zoom`,
                "POST",
                { level: params.level }
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

    pi.registerTool({
        name: "viewport.state",
        description: "Read the current canvas viewport state (focused node, position, zoom scale)",
        parameters: Type.Object({}),
        async execute(toolCallId, params) {
            const data = await callApi(
                `/api/projects/${PROJECT_ID}/viewport/state`,
                "GET"
            );
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });
}
