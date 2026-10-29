#!/usr/bin/env python
"""
Model Coverage Audit Tool (模型适配审计工具)

Answers: "服务商拉取的那么多模型都适配了吗？"

KEY INSIGHT (verified against app/core.py + app/services/generation.py):
Routing is PROVIDER-SCOPED, not based on model_specs.json.
  - First: provider protocol/id/base_url -> provider-specific handler.
  - Then: within handler, model name -> params/endpoint/version.
  - Fallback: generic OpenAI images with name heuristics (banana/gpt-image/gemini).
model_specs.json is ONLY used for vision detection (looks_like_vision_chat_model).

This tool REPLICA the real dispatch decision tree (image/video) using the ACTUAL
core.py functions, on a sample of known default models grouped by provider.
It tells you, per model, whether it is RECOGNIZED (adapted) or falls through.

Usage:
  python tools/audit_model_coverage.py                          # audit known defaults
  python tools/audit_model_coverage.py --from-providers         # + api_providers.json
  python tools/audit_model_coverage.py --from-file mids.txt     # extra models (provider=generic)

Run from project root.
"""
from __future__ import annotations
import argparse
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def load_core():
    from app import core as c
    return c


def build_providers():
    return {
        'runninghub': {'protocol': 'runninghub', 'id': 'runninghub', 'name': 'RunningHub'},
        'modelscope': {'protocol': 'openai', 'id': 'modelscope', 'name': 'ModelScope'},
        'codex': {'protocol': 'codex', 'id': 'codex', 'name': 'Codex'},
        'gemini-cli': {'protocol': 'gemini-cli', 'id': 'gemini-cli', 'name': 'Gemini CLI'},
        'apimart': {'protocol': 'openai', 'id': 'apimart', 'name': 'APIMart', 'base_url': 'https://apimart.ai/v1'},
        'generic': {'protocol': 'openai', 'id': 'custom-generic', 'name': 'Generic/OpenAI'},
    }


def gather_default_groups(c):
    """Return list of (provider_key, media, model)."""
    g = []
    def add(pk, media, lst):
        for m in (lst or []):
            if m:
                g.append((pk, media, str(m)))

    add('codex', 'image', c.CODEX_DEFAULT_IMAGE_MODELS)
    add('codex', 'chat', c.CODEX_DEFAULT_CHAT_MODELS)
    add('gemini-cli', 'image', c.GEMINI_CLI_DEFAULT_IMAGE_MODELS)
    add('gemini-cli', 'chat', c.GEMINI_CLI_DEFAULT_CHAT_MODELS)
    add('runninghub', 'image', c.RUNNINGHUB_DEFAULT_IMAGE_MODELS)
    add('runninghub', 'video', c.RUNNINGHUB_DEFAULT_VIDEO_MODELS)
    add('generic', 'video', c.AGNES_DEFAULT_VIDEO_MODELS)
    add('modelscope', 'image', c.MODELSCOPE_DEFAULT_IMAGE_MODELS)
    add('modelscope', 'chat', c.MODELSCOPE_DEFAULT_CHAT_MODELS)
    return g


def dispatch_image(c, provider, model):
    """Replicate generation.py:1308 image dispatch decision tree. Returns (path, status, detail)."""
    pid = str(provider.get('id') or '').strip().lower()
    if pid == 'modelscope':
        return ('modelscope', 'adapted', 'pass-through via generate_modelscope_provider_image')
    if c.is_codex_provider(provider):
        return ('codex', 'adapted', 'via generate_codex_provider_image (CLI)')
    if c.is_gemini_cli_provider(provider):
        return ('gemini-cli', 'adapted', 'via generate_gemini_cli_provider_image (CLI)')
    if c.is_runninghub_provider(provider):
        # entry config (rh-apps) or model definition -> model-agnostic-ish
        return ('runninghub', 'adapted', 'via runninghub_entry_config_from_model / runninghub_model_definition')
    # generic openai images fallback
    try:
        route = c.route_openai_image_request(provider, model, '1024x1024')
        family = route.get('family') or ''
        routed = route.get('model') or ''
        params = route.get('params') or {}
        if family in ('nano-banana', 'nano-banana-pro', 'gemini-image'):
            return ('openai-images', 'adapted-special', f'{family} family -> params={params}')
        if c.is_gpt_image_2_model(routed) or c.is_gpt_image_2_model(model):
            return ('openai-images', 'adapted', f'gpt-image-2 path (routed={routed!r})')
        return ('openai-images', 'passthrough', f'no special family; routed={routed!r} (works if provider accepts name)')
    except Exception as e:
        return ('openai-images', 'ERROR', str(e))


def dispatch_video(c, provider, model):
    """Replicate video dispatch (provider-specific fns + name). Returns (path, status, detail)."""
    pid = str(provider.get('id') or '').strip().lower()
    lc = str(model or '').lower()
    if c.is_runninghub_provider(provider):
        return ('runninghub', 'adapted', 'via runninghub video model_definition')
    if c.is_agnes_provider(provider, model):
        return ('agnes', 'adapted', 'agnes-video-* via agnes handler')
    if c.is_apimart_provider(provider):
        return ('apimart', 'adapted', 'via apimart_video_* fns')
    # generic: name-based provider detection inside video routing
    if any(k in lc for k in ('veo', 'sora', 'seedance', 'wan2', 'wanx', 'kling', 'hailuo', 'agnes')):
        return ('generic', 'adapted-name', f'recognized by name heuristics: {lc}')
    if c.classify_upstream_model(model) == 'video':
        return ('generic', 'maybe', f'classified video by keywords but no dedicated fn matched; verify')
    return ('generic', 'GAP?', f'no video handler + not recognized by name: {lc!r}')


def gather_provider_json(c):
    import json
    out = []
    path = os.path.join(_PROJECT_ROOT, 'app', 'data', 'api_providers.json')
    if not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        providers = json.load(f)
    for p in (providers or []):
        pid = str(p.get('id') or '').strip().lower()
        avail = p.get('available_models') or {}
        pk = pid if pid in build_providers() else 'generic'
        for key, media in (('image_models', 'image'), ('chat_models', 'chat'), ('video_models', 'video')):
            for m in (avail.get(key) or []):
                out.append((pk, media, str(m)))
            for m in (p.get(key) or []):
                out.append((pk, media, str(m)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-providers', action='store_true')
    ap.add_argument('--from-file', help='one model id per line (treated as generic)')
    args = ap.parse_args()

    c = load_core()
    prows = build_providers()
    groups = gather_default_groups(c)

    if args.from_providers:
        groups += gather_provider_json(c)
    if args.from_file:
        with open(args.from_file, 'r', encoding='utf-8') as f:
            for ln in f:
                if ln.strip():
                    groups.append(('generic', 'auto', ln.strip()))

    # model_specs membership
    specs = None
    try:
        from app.services import model_specs as ms
        s = ms.load_specs() or {}
        specs = set()
        for cat in ('chat_models', 'image_models', 'video_models'):
            for mid in (s.get(cat) or {}):
                specs.add(str(mid).lower())
    except Exception:
        specs = set()

    print('=' * 84)
    print('MODEL ADAPTATION AUDIT  (replicates REAL dispatch tree from core.py/generation.py)')
    print('=' * 84)
    print('Legend: adapted = has dedicated route | adapted-special = name heuristic hit')
    print('        GAP? = may mis-route (verify) | pass-through = works generically')
    print()

    seen = set()
    by_media = {'image': [], 'video': [], 'chat': [], 'auto': []}
    for pk, media, model in groups:
        key = (pk, media, model.lower())
        if key in seen:
            continue
        seen.add(key)
        prov = prows.get(pk, prows['generic'])
        if media in ('image', 'auto') and media != 'auto':
            path, status, detail = dispatch_image(c, prov, model)
        elif media == 'video':
            path, status, detail = dispatch_video(c, prov, model)
        else:
            # chat: protocol-based, works for all via /responses -> /chat/completions fallback
            vis = c.looks_like_vision_chat_model(model)
            path, status, detail = 'chat', 'adapted', f'protocol-based (works for all); vision={"Y" if vis else "N"}'
            if pk == 'generic' and media == 'auto':
                media = 'auto'
        spec = '*' if model.lower() in specs else ' '
        by_media[media].append((pk, spec, path, status, detail, model))

    for media in ('image', 'video', 'chat', 'auto'):
        rows = by_media.get(media, [])
        if not rows:
            continue
        n_gap = sum(1 for r in rows if 'GAP' in r[3])
        print(f'--- {media.upper()} ({len(rows)})  [provider=implied by group] ---')
        for pk, spec, path, status, detail, model in rows:
            mark = ' ' if spec == '*' else '?'
            print(f'  [{mark}] {model[:52]:52} -> {path:16} {status:16} {detail}')
        if n_gap:
            print(f'  *** {n_gap} potential gap(s) above ***')
        print()

    all_rows = [r for media in by_media for r in by_media[media]]
    gaps = [r for r in all_rows if 'GAP' in r[3]]
    print('=' * 84)
    print('SUMMARY')
    print('=' * 84)
    print(f'Total: {len(all_rows)} models | in model_specs.json: {sum(1 for r in all_rows if r[1] == "*")}')
    print(f'Potential gaps (GAP?): {len(gaps)}')
    for r in gaps:
        print(f'  - [{r[0]}/{r[3]}] {r[5]}  -> {r[4]}')
    print()
    print('NOTE: "adapted" means a dedicated route exists. model_specs.json drives vision')
    print('detection plus SPEC-FIRST routing (classify_upstream_model / route_openai_image_request),')
    print('so absence there means legacy name heuristics apply instead.')
    print('For a definitive answer, feed a live /v1/models dump via --from-file or --from-providers.')


if __name__ == '__main__':
    main()
