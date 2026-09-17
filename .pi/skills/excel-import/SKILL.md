---
name: excel-import
description: Import an Excel plan into the Firefly-Canvas as ready-to-generate image tasks. Use when the user provides an Excel file or link and wants to generate images from it.
---

# Excel Import to Canvas

Import an Excel plan, expand it into canvas nodes, and prepare for generation. Each task becomes: prompt node + reference image node → generation node.

## Workflow

### 1. Parse the Excel plan

Upload the file to get the parsed plan:

```bash
curl -X POST http://localhost:8000/api/excel/import-plan \
  -F "file=@<path-to-xlsx>"
```

Done when you have the parsed plan JSON with tasks (each has `base_prompt`, `target_aspect_ratio`, and optional `generation_references`).

### 2. Preview and confirm

Show the user: task count, sheet names, any tasks missing reference images. Ask for confirmation before expanding.

Done when the user confirms (or you proceed without confirmation if the task is trivial).

### 3. Expand into canvas nodes

```bash
curl -X POST http://localhost:8000/api/excel/expand-plan \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project-id>",
    "plan": <parsed-plan-from-step-1>
  }'
```

Each task becomes three nodes:
- `smart-prompt` with `text = base_prompt`
- `smart-image` for each reference image (uploaded as asset first)
- `smart-image` for generation with `runSettings{model: "nano-banana-2", aspect_ratio, prompt}` and `pending=1`

Edges: prompt → generation, reference image → generation. Only per-task generation nodes are created.

Done when the API returns success and the canvas shows the new nodes.

### 4. Generate

Select the generation nodes and trigger generation (UI "Generate" button or batch generation API). Generation runs with each node's `runSettings`.

Done when all generation nodes have produced output or failed.

## Error handling

- **409 Conflict**: Canvas modified concurrently. Tell the user to refresh and retry.
- **Missing reference image**: Skip the image node, keep the prompt → generation edge.
- **Parse failure**: File is not a valid .xlsx. Ask the user for a correct file.
