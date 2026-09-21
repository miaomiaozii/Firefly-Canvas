import type { ExtensionAPI } from "pi-stable";
import { Type } from "typebox";

// Base URL for FastAPI backend (injected via environment or config)
const API_BASE = process.env.FIREFLY_AGENT_API_BASE || "http://127.0.0.1:8000";
const PROJECT_ID = process.env.FIREFLY_AGENT_PROJECT_ID || "";

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

    pi.registerTool({
        name: "canvas.get_selection",
        description: "Get the currently selected nodes on the canvas",
        parameters: Type.Object({}),
        async execute() {
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/selection`, "GET");
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
                details: {},
            };
        },
    });

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
            confirm: Type.Optional(Type.Boolean({ description: "Set to true to confirm deletion" })),
        }),
        async execute(toolCallId, params) {
            if (!params.confirm) {
                return {
                    content: [{
                        type: "text",
                        text: `Confirmation required. Call again with confirm: true to delete node ${params.node_id}.`,
                    }],
                    details: { requires_confirmation: true },
                };
            }
            const data = await callApi(`/api/projects/${PROJECT_ID}/canvas/nodes/${params.node_id}`, "DELETE");
            return {
                content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
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
}
