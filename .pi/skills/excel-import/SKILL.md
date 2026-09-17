---
name: excel-import
description: Import an Excel plan file into the Firefly-Canvas as a set of ready-to-generate image tasks. Use when the user provides an Excel file or link and wants to generate images from it. Handles plan parsing, node expansion, and generation setup.
---

# Excel Import to Canvas

Import an Excel plan file into the Firefly-Canvas, expanding it into ready-to-generate image tasks with proper topology, parameters, and reference images.

## Trigger

Use this skill when the user provides an Excel file (.xlsx) or a link to one and wants to import it as a generation plan into the canvas.

## Workflow

### Step 1: Parse the Excel Plan

Upload the Excel file to parse it into a Plan IR (Intermediate Representation):

```bash
curl -X POST http://localhost:8000/api/excel/import-plan \
  -F "file=@<path-to-xlsx>"
```

This returns the parsed plan with tasks, each containing:
- `base_prompt`: The prompt text for the task
- `target_aspect_ratio`: The desired output aspect ratio
- `generation_references`: Reference image bytes (if any)

### Step 2: Confirm with User (Optional)

Before expanding, you may want to show the user a preview:
- Number of tasks found
- Sheet names
- Any tasks missing reference images

Ask for confirmation before proceeding to expansion.

### Step 3: Expand Plan into Canvas Nodes

Expand the parsed plan into canvas nodes for the target project:

```bash
curl -X POST http://localhost:8000/api/excel/expand-plan \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project-id>",
    "plan": <parsed-plan-from-step-1>
  }'
```

This creates the following topology for each task:
- A `smart-prompt` node containing the task's prompt text
- A `smart-image` node for each reference image (if present)
- A `smart-image` node for generation, connected from the prompt and reference image nodes
- Edges: prompt → generation, reference image → generation

The generation node is created with `runSettings`:
- `model`: defaults to `nano-banana-pro` (can be overridden by the plan)
- `aspect_ratio`: from the task's `target_aspect_ratio`
- `prompt`: from the task's `base_prompt`

No plan nodes or agent nodes are created — only the per-task generation topology.

### Step 4: Handle Errors

- **409 Conflict**: The canvas was modified concurrently. Tell the user to refresh and retry.
- **Missing reference images**: Tasks without reference images are skipped (no error). The prompt → generation edge is still created.
- **Parse failure**: The file is not a valid .xlsx. Ask the user to provide a correct file.

### Step 5: Generate Images

After expansion, the generation nodes are ready to run. Select the generation nodes and trigger generation via the canvas UI or API.

## Result Topology

For each task in the Excel plan:

```
[prompt: smart-prompt] ──┐
                         ├──→ [generation: smart-image]
[ref image: smart-image] ─┘
```

- Prompt node: `smart-prompt` with `text = base_prompt`
- Reference image node: `smart-image` (image upload form), created from uploaded asset
- Generation node: `smart-image` with `runSettings{model, aspect_ratio, prompt}` and `pending=1`

## Parameters

### Generation Node runSettings

| Field | Description |
|-------|-------------|
| `model` | Model ID for generation (default: `nano-banana-pro`) |
| `aspect_ratio` | Output image aspect ratio (e.g., "16:9", "1:1") |
| `prompt` | The prompt text for generation |

### Reference Images

Reference images are uploaded as project assets and connected to the generation node. They are used by the generation model as visual references.

## Error Handling

| Error | Meaning | Action |
|-------|---------|--------|
| 409 Conflict | Canvas modified concurrently | Tell user to refresh and retry |
| Missing reference image | Task has no reference | Skip image node, continue with prompt only |
| Invalid .xlsx | File is not a valid Excel plan | Ask user to provide correct file |

## Generation Entry Point

After expansion, generation nodes are created with `pending=1`. To generate:
1. Select the generation nodes in the canvas
2. Click "Generate" or use the batch generation API
3. The generation runs with the node's `runSettings`
