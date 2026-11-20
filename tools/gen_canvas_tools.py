#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#356 codegen：把 app/services/agent_tools_manifest.py 的 TOOLS 清单编译成
pi 扩展的工具注册数据 app/agent_extensions/canvas-tools.generated.ts。

用法：
    python tools/gen_canvas_tools.py            # 重新生成并写入
    python tools/gen_canvas_tools.py --check    # 只比对，漂移则 exit 1（进 pytest/fast 门）

单一来源约束：MCP（app/routers/api_mcp.py）与 pi 扩展共用同一份 name/description/
参数 schema；execute 体留在手写的 canvas-tools.ts EXECUTORS 映射里，由
tests/test_tool_surface_consistency.py 钉住「每个清单工具都有执行器」。
"""
import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services.agent_tools_manifest import TOOLS  # noqa: E402

OUT = os.path.join(ROOT, "app", "agent_extensions", "canvas-tools.generated.ts")

HEADER = '''// AUTO-GENERATED — 自动生成，勿手改。
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
'''

FOOTER = '''];
'''


def ts_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def opts_of(spec: dict, keys=("description", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "uniqueItems")) -> str:
    opts = {k: spec[k] for k in keys if k in spec}
    if not opts:
        return ""
    parts = []
    for k, v in opts.items():
        parts.append(f"{k}: {ts_str(v) if isinstance(v, str) else repr(v).lower() if isinstance(v, bool) else repr(v)}")
    return "{" + ", ".join(parts) + "}"


def _with_opts(fn: str, first: str, opts: str) -> str:
    return f"{fn}({first}{(', ' + opts) if opts else ''})"


def _scalar(fn: str, opts: str) -> str:
    """TypeBox 标量：options 是**第一个**参数（Type.String({...})），不是第二个。"""
    return f"{fn}({opts})" if opts else f"{fn}()"


def render_prop(spec: dict, *, union_branch=False) -> str:
    """JSON Schema 属性 → TypeBox 表达式。"""
    t = spec.get("type")
    opts = opts_of(spec)
    if "oneOf" in spec and t is None:
        # A7 anchor branches have disjoint types, so Union preserves oneOf.
        variants = ", ".join(render_prop(branch, union_branch=True) for branch in spec["oneOf"])
        return _with_opts("Type.Union", f"[{variants}]", opts)
    if "enum" in spec:
        lits = ", ".join(
            f"Type.Literal({ts_str(v)})" if isinstance(v, str) else (
                f"Type.Literal({repr(v).lower()})" if isinstance(v, bool) else f"Type.Literal({v})"
            )
            for v in spec["enum"]
        )
        return _with_opts("Type.Union", f"[{lits}]", opts)
    if t == "string":
        return _scalar("Type.String", opts)
    if t == "integer":
        return _scalar("Type.Integer", opts)
    if t == "number":
        return _scalar("Type.Number", opts)
    if t == "boolean":
        return _scalar("Type.Boolean", opts)
    if t == "array":
        items = render_prop(spec.get("items") or {"type": "string"})
        return _with_opts("Type.Array", items, opts)
    if t == "object":
        if union_branch:
            opts = opts_of(spec, ("description", "additionalProperties"))
        return _with_opts("Type.Object", render_props(spec), opts)
    raise ValueError(f"unhandled schema type: {spec!r}")


def render_props(schema: dict) -> str:
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        return "{}"
    lines = []
    for key, spec in props.items():
        expr = render_prop(spec)
        if key not in required:
            expr = f"Type.Optional({expr})"
        lines.append(f"{key}: {expr}")
    return "{ " + ", ".join(lines) + " }"


def render() -> str:
    out = io.StringIO()
    out.write(HEADER)
    for tool in TOOLS:
        out.write("    {\n")
        out.write(f"        name: {ts_str(tool['name'])},\n")
        out.write(f"        description: {ts_str(tool['description'])},\n")
        out.write(f"        parameters: Type.Object({render_props(tool['inputSchema'])}),\n")
        out.write("    },\n")
    out.write(FOOTER)
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写；漂移 exit 1")
    args = ap.parse_args()
    want = render()
    have = ""
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            have = f.read()
    if args.check:
        if have != want:
            print("DRIFT: canvas-tools.generated.ts 与清单不一致，请运行 python tools/gen_canvas_tools.py", file=sys.stderr)
            return 1
        print("OK: generated file up to date")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(want)
    print(f"wrote {OUT} ({len(TOOLS)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
