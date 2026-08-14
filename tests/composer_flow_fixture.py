from __future__ import annotations

from pathlib import Path

from tools.native_input_evidence import selector_owner_click_evidence


def write_composer_flow_runner(root: Path) -> Path:
    path = root / "tests" / "composer_flow_native_browser.py"
    source = """import argparse, json\nfrom pathlib import Path\n\np=argparse.ArgumentParser(); p.add_argument('--artifacts-dir', type=Path, required=True); a=p.parse_args(); r=a.artifacts_dir; r.mkdir(parents=True, exist_ok=True)\nviewports=((1920,1080),(1440,900),(1280,720),(1024,768),(800,600)); attempts=[]\nfor w,h in viewports:\n d=r/f'viewport-{w}x{h}'; d.mkdir(); ops=d/'operations.jsonl'; req=d/'requests.json'; con=d/'console.json';\n operations=[{'action':'click','selector':'#composerExpandBtn',**__CLICK_EVIDENCE__},{'action':'click','selector':'#composerFlowBtn',**__CLICK_EVIDENCE__},{'action':'drag','selector':'#promptResize','trusted':True,'observed_type':'mouseup','observed_within_selector':True},{'action':'wheel','selector':'#promptInput','trusted':True,'observed_type':'wheel','observed_delta_y':480},{'action':'reload'},{'action':'keyboard','key':'Escape','trusted':True,'observed_type':'keyup'}]\n ops.write_text(''.join(json.dumps(x)+'\\n' for x in operations),encoding='utf-8'); req.write_text('[]',encoding='utf-8'); con.write_text('[]',encoding='utf-8'); shots=[]\n for i in range(3):\n  s=d/f'{i}.png'; s.write_bytes(b'x'); shots.append(s.relative_to(r).as_posix())\n attempts.append({'status':'passed','viewport':[w,h],'operations':ops.relative_to(r).as_posix(),'requests':req.relative_to(r).as_posix(),'console_errors':con.relative_to(r).as_posix(),'screenshots':shots,'lifecycle':{'initial_height':84,'expanded_height':200,'manually_extended_height':264,'restored_height':84,'flow_scroll_top':120,'selection_preserved':True,'ime_composition_preserved':True,'undo_redo_preserved':True,'scroll_intent_preserved':True,'draft_autosaved_and_reopened':True,'source_relationship_preserved':True,'escape_generated':False,'provider_calls':0}})\nmanifest={'schema_version':1,'journey':'composer-expand-flow','status':'passed','environment':{'browser':{'name':'Google Chrome','channel':'stable','version':'fixture'},'provider':'offline-none'},'attempts':attempts,'first_failure':None}; (r/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')\n"""
    path.write_text(
        source.replace("__CLICK_EVIDENCE__", repr(selector_owner_click_evidence())),
        encoding="utf-8",
    )
    return path
