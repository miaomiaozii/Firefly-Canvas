"""Pi's actual executors must use the REST router's existing identity transport."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

import main
from app.routers import api_agent_tools
from app.services.agent_host import agent_host_manager
from app.services.agent_ops_context import current_agent_ops_context


ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "agent /?&中"
PROJECT = "project-393"

# Strip TypeScript without rewriting the executor: register every real executor and
# intercept the fetch it performs. A separate test feeds those requests to FastAPI.
NODE_RUNNER = r'''
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import vm from "node:vm";
let source = readFileSync(process.argv[1], "utf8");
const input = JSON.parse(process.argv[3]);
const names = input.map(([name]) => name);
source = source.replace(/^import .*;\r?\n/gm, "")
    .replace("export default function (pi: ExtensionAPI)", "function register(pi: ExtensionAPI)");
const calls = [], tools = {};
const context = {
    process: { env: { FIREFLY_AGENT_API_BASE: "http://127.0.0.1:3000",
                      FIREFLY_AGENT_PROJECT_ID: "project-393",
                      FIREFLY_AGENT_NODE_ID: process.argv[2] } },
    CANVAS_TOOL_DEFS: names.map(name => ({name, parameters: {properties: {}}})),
    URLSearchParams, encodeURIComponent,
    fetch: async (url, options) => {
        calls.push({url, method: options.method, headers: options.headers,
                    body: options.body === undefined ? null : JSON.parse(options.body)});
        return {ok: true, json: async () => ({success: true})};
    },
};
vm.createContext(context);
vm.runInContext(stripTypeScriptTypes(source), context);
context.register({registerTool: tool => { tools[tool.name] = tool.execute; }});
for (const [name, params] of input) await tools[name]("call", params);
console.log(JSON.stringify(calls));
'''

CASES = [
    ("canvas.create_node", {"type": "prompt", "title": "T", "x": 0, "text": "body"}),
    ("canvas.update_node", {"node_id": "n1", "title": "new", "text": "", "prompt": "p"}),
    ("canvas.connect", {"from_node": "n1", "to_node": "n2", "kind": "reference"}),
    ("canvas.delete_node", {"node_id": "n1"}),
    ("canvas.disconnect", {"from_node": "n1", "to_node": "n2"}),
    ("canvas.edit_crop", {"node_id": "n1", "rect": [1, 2, 3, 4]}),
    ("canvas.batch_edit", {"node_ids": ["n1"], "prompt": "edit"}),
    ("canvas.batch_prompt_edit", {"apply": True, "diff_id": "preview"}),
    ("plan.import_excel", {"attachment_id": "a" * 32}),
    ("plan.edit_preview", {"plan_id": "preview", "ops": [{"task_id": "t", "select": True}]}),
    ("plan.apply", {"plan_id": "preview", "generation": 2}),
    ("canvas.edit_rotate", {"node_id": "n1", "angle": 90}),
    ("canvas.edit_flip", {"node_id": "n1", "direction": "horizontal"}),
    ("canvas.edit_outpaint", {"node_id": "n1", "outpaint_size": 50, "offset": [1, 2]}),
    ("canvas.edit_erase", {"node_id": "n1", "rect": [1, 2, 3, 4], "prompt": "erase"}),
    ("generate.trigger", {"node_id": "n1"}),
    ("media.upload", {"file_path": "a.png"}),
    ("media.delete", {"asset_id": "asset-1"}),
    ("ops.undo", {"op_id": "op-1"}),
    ("viewport.focus", {"node_id": "n1"}),
    ("viewport.zoom", {"level": 2}),
]


def requests_from_extension(node_id=NODE_ID):
    result = subprocess.run(
        ["node", "--disable-warning=ExperimentalWarning", "--input-type=module", "-e", NODE_RUNNER,
         str(ROOT / "app/agent_extensions/canvas-tools.ts"), node_id, json.dumps(CASES)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
    )
    return dict(zip((name for name, _ in CASES), json.loads(result.stdout), strict=True))


class PiIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = requests_from_extension()

    def test_exact_write_requests(self):
        base = f"http://127.0.0.1:3000/api/projects/{PROJECT}"
        q = "?agent_node_id=agent%20%2F%3F%26%E4%B8%AD"
        expected = {
            "canvas.create_node": ("POST", "/canvas/nodes", {"node": {"type": "prompt", "title": "T", "x": 0, "text": "body"}, "agent_node_id": NODE_ID}),
            "canvas.update_node": ("PATCH", "/canvas/nodes/n1" + q, {"title": "new", "text": "", "prompt": "p"}),
            "canvas.connect": ("POST", "/canvas/edges" + q, {"edge": {"from": "n1", "to": "n2", "kind": "reference"}}),
            "canvas.delete_node": ("DELETE", "/canvas/nodes/n1" + q, None),
            "canvas.disconnect": ("DELETE", "/canvas/edges/n1/n2" + q, None),
            "canvas.edit_crop": ("POST", "/canvas/edit_crop" + q, {"node_id": "n1", "rect": [1, 2, 3, 4]}),
            "canvas.batch_edit": ("POST", "/canvas/batch_edit" + q, {"node_ids": ["n1"], "prompt": "edit"}),
            "canvas.batch_prompt_edit": ("POST", "/canvas/batch_prompt_edit" + q, {"apply": True, "diff_id": "preview"}),
            "plan.import_excel": ("POST", "/plan/import_excel" + q, {"attachment_id": "a" * 32}),
            "plan.edit_preview": ("POST", "/plan/edit_preview" + q, {"plan_id": "preview", "ops": [{"task_id": "t", "select": True}]}),
            "plan.apply": ("POST", "/plan/apply" + q, {"plan_id": "preview", "generation": 2}),
            "canvas.edit_rotate": ("POST", "/canvas/edit_rotate" + q, {"node_id": "n1", "angle": 90}),
            "canvas.edit_flip": ("POST", "/canvas/edit_flip" + q, {"node_id": "n1", "direction": "horizontal"}),
            "canvas.edit_outpaint": ("POST", "/canvas/edit_outpaint" + q, {"node_id": "n1", "outpaint_size": 50, "offset": [1, 2]}),
            "canvas.edit_erase": ("POST", "/canvas/edit_erase" + q, {"node_id": "n1", "rect": [1, 2, 3, 4], "prompt": "erase"}),
            "generate.trigger": ("POST", "/generate/trigger" + q, {"node_id": "n1"}),
            "media.upload": ("POST", "/media/upload", {"file_path": "a.png"}),
            "media.delete": ("DELETE", "/media/asset-1" + q, None),
            "ops.undo": ("POST", "/ops/undo", {"op_id": "op-1"}),
            "viewport.focus": ("POST", "/viewport/focus", {"node_id": "n1"}),
            "viewport.zoom": ("POST", "/viewport/zoom", {"level": 2}),
        }
        self.assertEqual(set(self.calls), set(expected))
        for name, (method, path, body) in expected.items():
            with self.subTest(tool=name):
                self.assertEqual(self.calls[name], {"url": base + path, "method": method,
                    "headers": {"Content-Type": "application/json", "X-Agent-Project": PROJECT}, "body": body})

    def test_empty_identity_is_not_fabricated(self):
        calls = requests_from_extension("")
        self.assertEqual(calls["canvas.create_node"]["body"], {"node": {"type": "prompt", "title": "T", "x": 0, "text": "body"}})
        for name in ("canvas.update_node", "canvas.connect", "canvas.delete_node", "canvas.disconnect"):
            self.assertEqual(urlsplit(calls[name]["url"]).query, "", name)

    def test_journal_routes_receive_actual_actor_and_turn(self):
        seen = []
        gates = []

        async def core(*args, **kwargs):
            ctx = current_agent_ops_context()
            seen.append((ctx.tool, ctx.project_id, ctx.agent_node_id, ctx.turn_id, args, kwargs))
            return {"success": True}

        async def gate(request, project_id, tool, args, message):
            gates.append((tool, request.query_params.get("agent_node_id")))

        patches = [patch.object(api_agent_tools, name, new=core) for name in (
            "create_node_core", "update_node_core", "create_edge_core", "delete_node_core", "delete_edge_core")]
        with patch.multiple(api_agent_tools, _server_confirm_gate=gate, _validate_project=lambda _: None), \
             patch.object(agent_host_manager, "get_current_turn_id", return_value="turn-393") as turn:
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            client = TestClient(main.app, base_url="http://127.0.0.1:3000")
            names = ("canvas.create_node", "canvas.update_node", "canvas.connect", "canvas.delete_node", "canvas.disconnect")
            for name in names:
                call = self.calls[name]
                response = client.request(call["method"], call["url"], headers=call["headers"], json=call["body"])
                self.assertEqual(response.status_code, 200, (name, response.text))
            self.assertEqual([item[:4] for item in seen], [(name, PROJECT, NODE_ID, "turn-393") for name in names])
            self.assertEqual(seen[0][5], {"agent_node_id": NODE_ID})
            self.assertEqual(seen[1][4], (PROJECT, "n1", {"title": "new", "text": "", "prompt": "p"}))
            self.assertEqual(seen[2][4], (PROJECT, {"from": "n1", "to": "n2", "kind": "reference"}))
            self.assertEqual(gates, [("canvas.delete_node", NODE_ID), ("canvas.delete_edge", NODE_ID)])
            self.assertEqual(turn.call_count, 5)
            for call in turn.call_args_list:
                self.assertEqual(call.args, (PROJECT, NODE_ID))

    def test_missing_actor_is_rejected_before_journal_mutation(self):
        calls = requests_from_extension("")
        async def core(*args, **kwargs):
            self.fail("mutation must not run without actor identity")

        with patch.object(api_agent_tools, "update_node_core", new=core), \
             patch.object(api_agent_tools, "create_edge_core", new=core):
            client = TestClient(main.app, base_url="http://127.0.0.1:3000")
            for name in ("canvas.update_node", "canvas.connect"):
                call = calls[name]
                response = client.request(call["method"], call["url"], headers=call["headers"], json=call["body"])
                self.assertEqual(response.status_code, 400, (name, response.text))
                self.assertEqual(response.json()["detail"]["code"], "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
