import type { ExtensionAPI } from "pi-stable";
import { Type } from "typebox";

// Base URL for FastAPI backend (injected via environment or config)
const API_BASE = process.env.FIREFLY_AGENT_API_BASE || "http://127.0.0.1:8000";
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
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
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
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
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
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
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
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
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
