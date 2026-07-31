import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MUTATION_CLIENT = ROOT / "static" / "js" / "project-canvas-mutations.js"
SMART_CANVAS = ROOT / "static" / "js" / "smart-canvas.js"
SMART_CANVAS_HTML = ROOT / "static" / "smart-canvas.html"


class ProjectCanvasMutationClientTests(unittest.TestCase):
    def run_node(self, source):
        result = subprocess.run(
            ["node", "-e", source],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_flush_retries_unconfirmed_mutations_on_latest_revision_without_losing_server_changes(self):
        script = f"""
const {{flushProjectCanvasMutations}} = require({json.dumps(str(MUTATION_CLIENT))});
const baseline = {{
  nodes: {{conversation: JSON.stringify({{id:'conversation', type:'smart-conversation', messages:[]}})}},
  edges: {{}},
  viewport: JSON.stringify({{x:0,y:0,scale:1}}),
  meta: JSON.stringify({{title:'Project', icon:'sparkles', settings:{{}}, logs:[]}})
}};
const initialCanvas = {{
  project_id:'project-1', revision:4,
  nodes:[{{id:'conversation', type:'smart-conversation', messages:[]}}],
  connections:[], viewport:{{x:0,y:0,scale:1}}, title:'Project', icon:'sparkles', settings:{{}}, logs:[]
}};
const latestCanvas = {{
  ...initialCanvas,
  revision:5,
  nodes:[
    {{id:'conversation', type:'smart-conversation', messages:[{{id:'reply', role:'assistant', text:'server reply'}}]}},
    {{id:'server-node', type:'smart-image', images:[]}}
  ],
  connections:[{{id:'server-edge', from:'server-node', to:'conversation', kind:'input'}}]
}};
const prompt = {{id:'prompt-new', type:'smart-prompt', text:'selected text', source_node_id:'conversation'}};
const calls = [];
const result = await flushProjectCanvasMutations({{
  canvas:initialCanvas,
  baseline,
  mutations:[{{op:'upsert_node', node:prompt}}],
  maxConflictRetries:2,
  send:async (baseRevision, mutations) => {{
    calls.push({{baseRevision, mutations}});
    if(calls.length === 1) return {{status:409, canvas:latestCanvas}};
    return {{status:200, revision:6, updated_at:1234, saved:true}};
  }}
}});
console.log(JSON.stringify({{calls, result}}));
"""
        payload = self.run_node(f"(async()=>{{{script}}})().catch(error=>{{console.error(error);process.exit(1)}})")

        self.assertEqual([call["baseRevision"] for call in payload["calls"]], [4, 5])
        self.assertEqual(payload["calls"][1]["mutations"], [{"op": "upsert_node", "node": {
            "id": "prompt-new", "type": "smart-prompt", "text": "selected text", "source_node_id": "conversation",
        }}])
        nodes = {node["id"]: node for node in payload["result"]["canvas"]["nodes"]}
        self.assertEqual(nodes["conversation"]["messages"][0]["text"], "server reply")
        self.assertIn("server-node", nodes)
        self.assertIn("prompt-new", nodes)
        self.assertEqual(payload["result"]["canvas"]["connections"], [
            {"id": "server-edge", "from": "server-node", "to": "conversation", "kind": "input"},
        ])
        self.assertEqual(payload["result"]["canvas"]["revision"], 6)

    def test_project_snapshot_excludes_canvas_ui_and_global_composer_defaults(self):
        script = f"""
const {{mutationsFromCanvas, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baseline = {{
  title:'Project', icon:'sparkles',
  viewport:{{x:12,y:34,scale:1.4}},
  settings:{{engine:'api', provider_id:'comfly', model:'image-model', promptH:240}},
  nodes:[{{id:'image', type:'smart-image', prompt:'keep prompt', runSettings:{{model:'image-model'}}}}],
  connections:[], logs:[{{id:'history-1', prompt:'keep history'}}]
}};
const changed = {{...baseline,
  viewport:{{x:800,y:-240,scale:0.48}},
  settings:{{engine:'runninghub', provider_id:'other', model:'another-model', promptH:360}}
}};
const snapshot = snapshotFromCanvas(baseline);
const changes = mutationsFromCanvas(changed, snapshot);
console.log(JSON.stringify({{snapshot, changes}}));
"""
        payload = self.run_node(script)
        self.assertNotIn("viewport", payload["snapshot"])
        meta = json.loads(payload["snapshot"]["meta"])
        self.assertNotIn("settings", meta)
        self.assertEqual(meta["title"], "Project")
        self.assertEqual(meta["logs"], [{"id": "history-1", "prompt": "keep history"}])
        self.assertEqual(payload["changes"]["mutations"], [])

    def test_rebase_combines_local_node_edit_with_concurrent_server_node_update(self):
        script = f"""
const {{rebaseProjectCanvasMutations, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baselineCanvas = {{
  nodes:[{{id:'shared', type:'smart-conversation', x:10, messages:[]}}],
  connections:[], viewport:{{x:0,y:0,scale:1}}, settings:{{}}, logs:[]
}};
const serverCanvas = {{
  ...baselineCanvas, revision:9,
  nodes:[{{id:'shared', type:'smart-conversation', x:10, messages:[{{id:'reply', role:'assistant', text:'kept'}}]}}]
}};
const localNode = {{id:'shared', type:'smart-conversation', x:80, messages:[]}};
const result = rebaseProjectCanvasMutations(
  serverCanvas,
  snapshotFromCanvas(baselineCanvas),
  [{{op:'upsert_node', node:localNode}}]
);
console.log(JSON.stringify(result));
"""
        payload = self.run_node(script)
        self.assertEqual(payload["mutations"][0]["node"]["x"], 80)
        self.assertEqual(payload["mutations"][0]["node"]["messages"][0]["text"], "kept")
        self.assertEqual(payload["canvas"]["nodes"][0], payload["mutations"][0]["node"])

    def test_rebase_merges_asset_id_arrays_without_resurrecting_server_deletions(self):
        script = f"""
const {{rebaseProjectCanvasMutations, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baselineCanvas = {{nodes:[{{id:'media', images:[
  {{asset_id:'a', name:'A'}}, {{asset_id:'b', name:'B'}}
]}}], connections:[], viewport:{{}}, settings:{{}}, logs:[]}};
const local = {{id:'media', images:[
  {{asset_id:'a', name:'A locally renamed'}}, {{asset_id:'b', name:'B'}}
]}};
const serverCanvas = {{...baselineCanvas, revision:5, nodes:[{{id:'media', images:[
  {{asset_id:'a', name:'A'}}, {{asset_id:'c', name:'C from server'}}
]}}]}};
const result = rebaseProjectCanvasMutations(
  serverCanvas, snapshotFromCanvas(baselineCanvas), [{{op:'upsert_node', node:local}}]
);
console.log(JSON.stringify(result.canvas.nodes[0].images));
"""
        images = self.run_node(script)
        self.assertEqual(images, [
            {"asset_id": "a", "name": "A locally renamed"},
            {"asset_id": "c", "name": "C from server"},
        ])

    def test_retry_limit_returns_latest_revision_with_local_intent_still_rebased(self):
        script = f"""
const {{flushProjectCanvasMutations}} = require({json.dumps(str(MUTATION_CLIENT))});
const canvas = {{revision:1, nodes:[], connections:[], viewport:{{}}, settings:{{}}, logs:[]}};
const calls = [];
const result = await flushProjectCanvasMutations({{
  canvas,
  mutations:[{{op:'upsert_node', node:{{id:'local', type:'smart-prompt', text:'keep me'}}}}],
  maxConflictRetries:2,
  send:async baseRevision => {{
    calls.push(baseRevision);
    const revision = baseRevision + 1;
    return {{status:409, canvas:{{...canvas, revision, nodes:[{{id:`server-${{revision}}`}}]}}}};
  }}
}});
console.log(JSON.stringify({{calls, result}}));
"""
        payload = self.run_node(f"(async()=>{{{script}}})().catch(error=>{{console.error(error);process.exit(1)}})")
        self.assertEqual(payload["calls"], [1, 2, 3])
        self.assertTrue(payload["result"]["conflictLimitReached"])
        self.assertEqual(payload["result"]["serverCanvas"]["revision"], 4)
        self.assertEqual(payload["result"]["canvas"]["revision"], 4)
        self.assertIn("local", [node["id"] for node in payload["result"]["canvas"]["nodes"]])

    def test_save_core_requeues_original_mutations_after_conflict_limit(self):
        source = SMART_CANVAS.read_text(encoding="utf-8")
        self.assertIn(
            "if(flushResult.mutations.length || lateChangeSet.mutations.length) scheduleStateSave();",
            source,
        )

    def test_durable_snapshot_ignores_runtime_node_status_but_keeps_content_edits(self):
        script = f"""
const {{durableSnapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const base = {{
  nodes:[{{id:'image', type:'smart-image', title:'猫', prompt:'一只猫', images:[]}}],
  connections:[], viewport:{{x:0,y:0,scale:1}}, settings:{{}}, logs:[]
}};
const runtimeOnly = {{...base, nodes:[{{...base.nodes[0], running:true, pending:1, queued:true,
  pendingTasks:[{{taskId:'task-1'}}], runStartedAt:123, runTimerHidden:false}}]}};
const contentEdit = {{...base, nodes:[{{...base.nodes[0], prompt:'一只坐在窗边的猫', running:true}}]}};
console.log(JSON.stringify({{
  runtimeEqual: JSON.stringify(durableSnapshotFromCanvas(base)) === JSON.stringify(durableSnapshotFromCanvas(runtimeOnly)),
  contentDifferent: JSON.stringify(durableSnapshotFromCanvas(base)) !== JSON.stringify(durableSnapshotFromCanvas(contentEdit))
}}));
"""
        payload = self.run_node(script)
        self.assertTrue(payload["runtimeEqual"])
        self.assertTrue(payload["contentDifferent"])

    def test_durable_snapshot_ignores_preview_measurements_but_keeps_media_dimensions(self):
        script = f"""
const {{durableSnapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const base = {{
  nodes:[{{id:'image', type:'smart-image', images:[{{asset_id:'asset-1', natural_w:480, natural_h:320}}]}}],
  connections:[], viewport:{{}}, settings:{{}}, logs:[]
}};
const measured = {{...base, nodes:[{{...base.nodes[0], images:[{{...base.nodes[0].images[0], _naturalSizeLoading:true, layout_w:480, layout_h:320}}]}}]}};
const edited = {{...base, nodes:[{{...base.nodes[0], images:[{{...base.nodes[0].images[0], natural_w:640}}]}}]}};
console.log(JSON.stringify({{
  measurementEqual: JSON.stringify(durableSnapshotFromCanvas(base)) === JSON.stringify(durableSnapshotFromCanvas(measured)),
  contentDifferent: JSON.stringify(durableSnapshotFromCanvas(base)) !== JSON.stringify(durableSnapshotFromCanvas(edited))
}}));
"""
        payload = self.run_node(script)
        self.assertTrue(payload["measurementEqual"])
        self.assertTrue(payload["contentDifferent"])

    def test_rebase_does_not_resurrect_server_deleted_nodes_or_edges(self):
        script = f"""
const {{rebaseProjectCanvasMutations, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const node = {{id:'deleted-node', type:'smart-prompt', text:'before'}};
const edge = {{id:'deleted-edge', from:'source', to:'deleted-node'}};
const baseline = {{nodes:[node], connections:[edge], viewport:{{}}, settings:{{}}, logs:[]}};
const server = {{...baseline, revision:8, nodes:[], connections:[]}};
const result = rebaseProjectCanvasMutations(server, snapshotFromCanvas(baseline), [
  {{op:'upsert_node', node:{{...node, text:'stale local edit'}}}},
  {{op:'upsert_edge', edge:{{...edge, kind:'input'}}}}
]);
console.log(JSON.stringify(result));
"""
        payload = self.run_node(script)
        self.assertEqual(payload["mutations"], [])
        self.assertEqual(payload["canvas"]["nodes"], [])
        self.assertEqual(payload["canvas"]["connections"], [])

    def test_conversation_response_rebases_unsaved_local_intent_and_keeps_it_pending(self):
        script = f"""
const {{rebaseProjectCanvasResponse, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baselineCanvas = {{revision:4, title:'Project', icon:'sparkles', settings:{{}}, logs:[], viewport:{{x:0,y:0,scale:1}},
  nodes:[
    {{id:'conversation', type:'smart-conversation', x:100, messages:[]}},
    {{id:'image', type:'smart-image', x:20, images:[{{asset_id:'asset-1', kind:'image'}}]}}
  ], connections:[]}};
const localCanvas = {{...baselineCanvas, nodes:[
  {{id:'conversation', type:'smart-conversation', x:260, messages:[]}},
  baselineCanvas.nodes[1],
  {{id:'prompt-new', type:'smart-prompt', text:'keep this draft'}}
], connections:[{{id:'local-edge', from:'image', to:'conversation', kind:'input'}}]}};
const serverCanvas = {{...baselineCanvas, revision:5, nodes:[
  {{id:'conversation', type:'smart-conversation', x:100, messages:[{{id:'assistant-1', role:'assistant', text:'server reply'}}]}},
  baselineCanvas.nodes[1]
]}};
const result = rebaseProjectCanvasResponse(serverCanvas, snapshotFromCanvas(baselineCanvas), localCanvas);
console.log(JSON.stringify(result));
"""
        payload = self.run_node(script)
        nodes = {node["id"]: node for node in payload["canvas"]["nodes"]}
        self.assertEqual(nodes["conversation"]["x"], 260)
        self.assertEqual(nodes["conversation"]["messages"][0]["text"], "server reply")
        self.assertEqual(nodes["prompt-new"]["text"], "keep this draft")
        self.assertEqual(payload["canvas"]["connections"], [
            {"id": "local-edge", "from": "image", "to": "conversation", "kind": "input"},
        ])
        self.assertEqual(payload["baseline"], payload["serverSnapshot"])
        self.assertIn("conversation", payload["baseline"]["nodes"])
        self.assertIn("server reply", payload["baseline"]["nodes"]["conversation"])
        self.assertEqual(
            [mutation["op"] for mutation in payload["mutations"]],
            ["upsert_node", "upsert_node", "upsert_edge"],
        )

    def test_agent_terminal_remote_state_wins_over_local_transient_poll_state(self):
        script = f"""
const {{rebaseProjectCanvasResponse, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baseline = {{revision:3, title:'Project', icon:'sparkles', settings:{{}}, logs:[], viewport:{{}}, nodes:[
  {{id:'agent', type:'smart-agent', agent_plan:{{plan_id:'p', status:'running', tasks:[{{task_id:'t', status:'running'}}]}}}},
  {{id:'t', type:'agent-task', task_id:'t', status:'running', error:''}},
  {{id:'r', type:'smart-image', agent_task_id:'t', generationState:'queued', images:[], pending:1, running:true}}
], connections:[]}};
const local = {{...baseline, nodes:[
  baseline.nodes[0],
  {{...baseline.nodes[1], status:'queued'}},
  {{...baseline.nodes[2], generationState:'running', pending:1, running:true}}
]}};
const remote = {{...baseline, revision:4, nodes:[
  {{id:'agent', type:'smart-agent', agent_plan:{{plan_id:'p', status:'completed', tasks:[{{task_id:'t', status:'succeeded'}}]}}}},
  {{id:'t', type:'agent-task', task_id:'t', status:'succeeded', error:'', result:{{media:[]}}}},
  {{id:'r', type:'smart-image', agent_task_id:'t', generationState:'succeeded', images:[{{asset_id:'out-1', kind:'image'}}], pending:0, running:false}}
], connections:[]}};
const result = rebaseProjectCanvasResponse(remote, snapshotFromCanvas(baseline), local);
console.log(JSON.stringify(result.canvas.nodes));
"""
        nodes = {item["id"]: item for item in self.run_node(script)}
        self.assertEqual(nodes["agent"]["agent_plan"]["status"], "completed")
        self.assertEqual(nodes["t"]["status"], "succeeded")
        self.assertEqual(nodes["r"]["generationState"], "succeeded")
        self.assertEqual(nodes["r"]["images"][0]["asset_id"], "out-1")

    def test_conversation_rebase_replaces_timeout_placeholder_by_request_and_role(self):
        script = f"""
const {{rebaseProjectCanvasResponse, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const baseline = {{revision:7, title:'Project', icon:'sparkles', settings:{{}}, logs:[], viewport:{{}},
  nodes:[{{id:'conversation', type:'smart-conversation', messages:[]}}], connections:[]}};
const local = {{...baseline, nodes:[{{id:'conversation', type:'smart-conversation', messages:[
  {{id:'local-request-1', role:'user', request_id:'request-1', status:'pending', text:'keep one user'}}
]}}]}};
const server = {{...baseline, revision:8, nodes:[{{id:'conversation', type:'smart-conversation', messages:[
  {{id:'server-user-1', role:'user', request_id:'request-1', status:'completed', text:'keep one user'}},
  {{id:'server-assistant-1', role:'assistant', request_id:'request-1', status:'completed', text:'done'}}
]}}]}};
const result = rebaseProjectCanvasResponse(server, snapshotFromCanvas(baseline), local);
console.log(JSON.stringify(result.canvas.nodes[0].messages));
"""
        messages = self.run_node(script)
        self.assertEqual(messages, [
            {
                "id": "server-user-1", "role": "user", "request_id": "request-1",
                "status": "completed", "text": "keep one user",
            },
            {
                "id": "server-assistant-1", "role": "assistant", "request_id": "request-1",
                "status": "completed", "text": "done",
            },
        ])

    def test_conversation_response_ignores_older_revision_without_losing_newer_messages(self):
        script = f"""
const {{rebaseProjectCanvasResponse, snapshotFromCanvas}} = require({json.dumps(str(MUTATION_CLIENT))});
const current = {{revision:3, title:'Project', icon:'sparkles', settings:{{}}, logs:[], viewport:{{}},
  nodes:[{{id:'conversation', type:'smart-conversation', messages:[
    {{id:'user-a', role:'user', request_id:'a', status:'completed', text:'A'}},
    {{id:'user-b', role:'user', request_id:'b', status:'pending', text:'B'}}
  ]}}], connections:[]}};
const stale = {{...current, revision:2, nodes:[{{id:'conversation', type:'smart-conversation', messages:[
  {{id:'user-a', role:'user', request_id:'a', status:'completed', text:'A'}}
]}}]}};
const result = rebaseProjectCanvasResponse(stale, snapshotFromCanvas(current), current);
console.log(JSON.stringify({{ignored:result.ignored, revision:result.canvas.revision, messages:result.canvas.nodes[0].messages}}));
"""
        payload = self.run_node(script)
        self.assertTrue(payload["ignored"])
        self.assertEqual(payload["revision"], 3)
        self.assertEqual([item["text"] for item in payload["messages"]], ["A", "B"])

    def test_conversation_post_waits_for_canvas_flush_and_uses_latest_connections(self):
        script = f"""
const {{requestAfterProjectCanvasFlush}} = require({json.dumps(str(MUTATION_CLIENT))});
let serverCanvas = {{nodes:[], connections:[]}};
let localCanvas = {{nodes:[{{id:'conversation-new', type:'smart-conversation'}}], connections:[]}};
const observed = [];
const send = async () => requestAfterProjectCanvasFlush({{
  flush:async () => {{ serverCanvas = JSON.parse(JSON.stringify(localCanvas)); return true; }},
  request:async () => {{
    const conversation = serverCanvas.nodes.find(node => node.id === 'conversation-new');
    const sourceIds = serverCanvas.connections.filter(edge => edge.to === 'conversation-new').map(edge => edge.from);
    observed.push({{conversation:Boolean(conversation), sourceIds}});
    return {{ok:true}};
  }}
}});
await send();
localCanvas = {{...localCanvas,
  nodes:[...localCanvas.nodes, {{id:'image-new', type:'smart-image', images:[{{asset_id:'asset-new'}}]}}],
  connections:[{{id:'edge-new', from:'image-new', to:'conversation-new', kind:'input'}}]
}};
await send();
let failedPostCalls = 0;
let failed = false;
try {{
  await requestAfterProjectCanvasFlush({{
    flush:async () => false,
    request:async () => {{ failedPostCalls += 1; }}
  }});
}} catch(error) {{ failed = true; }}
console.log(JSON.stringify({{observed, failed, failedPostCalls}}));
"""
        payload = self.run_node(f"(async()=>{{{script}}})().catch(error=>{{console.error(error);process.exit(1)}})")
        self.assertEqual(payload["observed"], [
            {"conversation": True, "sourceIds": []},
            {"conversation": True, "sourceIds": ["image-new"]},
        ])
        self.assertTrue(payload["failed"])
        self.assertEqual(payload["failedPostCalls"], 0)

    def test_accepted_mutations_use_explicit_flush_confirmation_before_reporting_saved(self):
        script = f"""
const {{flushProjectCanvasMutations}} = require({json.dumps(str(MUTATION_CLIENT))});
let confirmations = 0;
const result = await flushProjectCanvasMutations({{
  canvas:{{revision:2, nodes:[], connections:[], viewport:{{}}, settings:{{}}, logs:[]}},
  mutations:[{{op:'upsert_node', node:{{id:'conversation-new', type:'smart-conversation'}}}}],
  send:async () => ({{status:200, revision:3, updated_at:10, saved:false}}),
  confirmSaved:async response => {{ confirmations += 1; return response.revision === 3; }}
}});
console.log(JSON.stringify({{result, confirmations}}));
"""
        payload = self.run_node(f"(async()=>{{{script}}})().catch(error=>{{console.error(error);process.exit(1)}})")
        self.assertEqual(payload["confirmations"], 1)
        self.assertTrue(payload["result"]["saved"])
        self.assertEqual(payload["result"]["canvas"]["revision"], 3)

    def test_non_image_project_media_uses_original_resource_instead_of_image_preview(self):
        script = SMART_CANVAS.read_text(encoding="utf-8")
        page = SMART_CANVAS_HTML.read_text(encoding="utf-8")
        self.assertIn("function smartVideoPreviewHtml", script)
        self.assertIn("return smartVideoFallbackHtml(original, attrs);", script)
        self.assertIn("mediaKindForItem(hydrated) === 'image'", script)
        self.assertIn("(media.kind || mediaKindForItem(media)) === 'image'", script)
        self.assertNotIn("['image', 'video'].includes(mediaKindForItem(hydrated))", script)
        self.assertNotIn("preview_url:['image','video'].includes", script)
        self.assertLess(page.index("/static/js/project-canvas-mutations.js"), page.index("/static/js/smart-canvas.js"))


if __name__ == "__main__":
    unittest.main()
