"""Deterministic artifact fixture for Issue #33 long-content evidence tests."""
from __future__ import annotations

import json
from pathlib import Path

from tools.native_input_evidence import selector_owner_click_evidence

VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
FLAGS = (
    "prompt_same_dom",
    "prompt_selection_preserved",
    "prompt_ime_preserved",
    "prompt_undo_redo_preserved",
    "prompt_autosaved_and_reopened",
    "source_relationships_preserved",
    "connection_reader_complete",
    "history_prompt_reader_complete",
    "history_focus_restored_after_virtualization",
    "error_reader_complete",
    "readonly_views_immutable",
)


def write_long_content_artifacts(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    attempts = []
    for index, viewport in enumerate(VIEWPORTS):
        prefix = f"viewport-{viewport[0]}x{viewport[1]}"
        operations_path = root / f"{prefix}-operations.jsonl"
        requests_path = root / f"{prefix}-requests.json"
        console_path = root / f"{prefix}-console.json"
        screenshots = []
        for name in ("prompt-flow", "connection-reader", "history-reader", "focus-restored", "error-reader"):
            path = root / f"{prefix}-{name}.png"
            path.write_bytes(b"fixture")
            screenshots.append(path.name)
        operations = [
            {"action":"click","selector":'[data-id="reader-prompt"] .prompt-node-flow-toggle',**selector_owner_click_evidence()},
            {"action":"click","selector":'[data-long-content-open="input-prompt"]',**selector_owner_click_evidence()},
            {"action":"click","selector":'[data-canvas-log-id="reader-history"] [data-long-content-open="history-prompt"]',**selector_owner_click_evidence()},
            {"action":"click","selector":"#longContentReaderClose",**selector_owner_click_evidence()},
            {"action":"keyboard","key":"Enter","modifiers":[],"trusted":True,"observed_type":"keyup"},
            {"action":"keyboard","key":"Escape","modifiers":[],"trusted":True,"observed_type":"keyup"},
            {"action":"keyboard","key":"z","modifiers":["Control"],"trusted":True,"observed_type":"keyup"},
            {"action":"wheel","selector":'[data-id="reader-prompt"] .prompt-node-text',"trusted":True,"observed_type":"wheel","observed_delta_y":300},
            {"action":"wheel","selector":"#longContentReaderBody","trusted":True,"observed_type":"wheel","observed_delta_y":300},
            {"action":"reload","url":"http://127.0.0.1/smart-canvas"},
        ]
        operations_path.write_text("\n".join(json.dumps(item) for item in operations) + "\n", encoding="utf-8")
        requests_path.write_text(json.dumps([{"url":"http://127.0.0.1/api/projects/reader-project"}]), encoding="utf-8")
        console_path.write_text("[]", encoding="utf-8")
        lifecycle = {key: True for key in FLAGS}
        lifecycle.update({
            "prompt_scroll_top": 300,
            "connection_reader_scroll_top": 400,
            "history_prompt_scroll_top": 500,
            "error_reader_scroll_top": 600,
            "provider_calls": 0,
        })
        attempts.append({
            "viewport": list(viewport),
            "status": "passed",
            "lifecycle": lifecycle,
            "screenshots": screenshots,
            "operations": operations_path.name,
            "requests": requests_path.name,
            "console_errors": console_path.name,
        })
    manifest = {
        "journey": "long-content-reading",
        "status": "passed",
        "environment": {
            "browser": {"name":"Google Chrome","channel":"stable","version":"151.0.7922.77"},
            "provider": "offline-none",
        },
        "attempts": attempts,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_long_content_runner(root: Path) -> Path:
    path = root / "tests" / "long_content_native_browser.py"
    source = """import argparse, json\nfrom pathlib import Path\n\np=argparse.ArgumentParser(); p.add_argument('--artifacts-dir',type=Path,required=True); a=p.parse_args(); r=a.artifacts_dir; r.mkdir(parents=True,exist_ok=True)\nviewports=((1920,1080),(1440,900),(1280,720),(1024,768),(800,600)); attempts=[]\nflags=('prompt_same_dom','prompt_selection_preserved','prompt_ime_preserved','prompt_undo_redo_preserved','prompt_autosaved_and_reopened','source_relationships_preserved','connection_reader_complete','history_prompt_reader_complete','history_focus_restored_after_virtualization','error_reader_complete','readonly_views_immutable')\nfor w,h in viewports:\n d=r/f'viewport-{w}x{h}'; d.mkdir(); ops=d/'operations.jsonl'; req=d/'requests.json'; con=d/'console.json'\n operations=[{'action':'click','selector':'[data-id=\\\"reader-prompt\\\"] .prompt-node-flow-toggle','trusted':True,'observed_type':'mouseup'},{'action':'click','selector':'[data-long-content-open=\\\"input-prompt\\\"]','trusted':True,'observed_type':'mouseup'},{'action':'click','selector':'[data-canvas-log-id=\\\"reader-history\\\"] [data-long-content-open=\\\"history-prompt\\\"]','trusted':True,'observed_type':'mouseup'},{'action':'click','selector':'#longContentReaderClose','trusted':True,'observed_type':'mouseup'},{'action':'keyboard','key':'Enter','modifiers':[],'trusted':True,'observed_type':'keyup'},{'action':'keyboard','key':'Escape','modifiers':[],'trusted':True,'observed_type':'keyup'},{'action':'keyboard','key':'z','modifiers':['Control'],'trusted':True,'observed_type':'keyup'},{'action':'wheel','selector':'[data-id=\\\"reader-prompt\\\"] .prompt-node-text','trusted':True,'observed_type':'wheel','observed_delta_y':300},{'action':'wheel','selector':'#longContentReaderBody','trusted':True,'observed_type':'wheel','observed_delta_y':300},{'action':'reload'}]\n for item in operations:\n  if item.get('action')=='click': item.update(__CLICK_EVIDENCE__)\n ops.write_text(''.join(json.dumps(x)+'\\n' for x in operations),encoding='utf-8'); req.write_text('[]',encoding='utf-8'); con.write_text('[]',encoding='utf-8'); shots=[]\n for i in range(5):\n  s=d/f'{i}.png'; s.write_bytes(b'x'); shots.append(s.relative_to(r).as_posix())\n lifecycle={key:True for key in flags}; lifecycle.update({'prompt_scroll_top':300,'connection_reader_scroll_top':400,'history_prompt_scroll_top':500,'error_reader_scroll_top':600,'provider_calls':0})\n attempts.append({'status':'passed','viewport':[w,h],'operations':ops.relative_to(r).as_posix(),'requests':req.relative_to(r).as_posix(),'console_errors':con.relative_to(r).as_posix(),'screenshots':shots,'lifecycle':lifecycle})\nmanifest={'journey':'long-content-reading','status':'passed','environment':{'browser':{'name':'Google Chrome','channel':'stable','version':'fixture'},'provider':'offline-none'},'attempts':attempts,'first_failure':None}; (r/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')\n"""
    path.write_text(
        source.replace("__CLICK_EVIDENCE__", repr(selector_owner_click_evidence())),
        encoding="utf-8",
    )
    return path
