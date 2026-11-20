"""Resolve canvas reference positions before ownership checks or legacy import.

No I/O: URLs are labels/locators, never fetched. Browser and Agent decide what
to do with the same selected locators at their respective trust boundaries.
"""
import json
import math
import re
from functools import cmp_to_key
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlsplit

from app.services.agent_refs import managed_edges, managed_edge


CANVAS_REFERENCE_MAX = 20
_BLOCKS = {'div', 'p', 'li', 'section', 'article', 'header', 'footer', 'blockquote'}
_VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
_ASSET = re.compile(r'[A-Za-z0-9._-]{1,200}')
_KIND_EXTENSIONS = {
    'text': 'txt|json|csv|srt|vtt|md', 'audio': 'mp3|wav|m4a|aac|ogg|flac',
    'video': 'mp4|webm|mov|m4v|avi|mkv',
}


def _objects(items):
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _strings(items):
    return [item for item in items if isinstance(item, str) and item] if isinstance(items, list) else []


def _str(value):
    return value if isinstance(value, str) else ''


def _number(value, default=0):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else default
    except (ValueError, TypeError, OverflowError):
        return default


def _index(obj, fallback):
    value = _number(obj['imageIndex'], fallback) if 'imageIndex' in obj else fallback
    return int(value) if value == int(value) else value


def _url(item):
    return _str(item.get('url'))


def _asset(item):
    value = _str(item.get('asset_id'))
    return value if _ASSET.fullmatch(value) else ''


def _kind(item):
    kind = item.get('kind')
    if kind == 'file':
        return 'file'
    url = _url(item)
    try:
        parsed = urlsplit(url)
        if parsed.path == '/api/media-preview':
            url = parse_qs(parsed.query).get('url', [''])[0] or url
    except ValueError:
        pass
    for name, extensions in _KIND_EXTENSIONS.items():
        if kind == name or re.search(rf'\.(?:{extensions})(\?|$)', url, re.I):
            return name
    return 'image'


def _trim(text):
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+\n', '\n', text)).strip()


class _PromptParts(HTMLParser):
    """Extract editor text; token descendants are opaque, even with nested tags."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.feed(html)
        self.close()
        while self.stack:
            self.handle_endtag(self.stack[-1][0])

    def handle_starttag(self, tag, pairs):
        attrs = {}
        for key, value in pairs:
            attrs.setdefault(key, value or '')
        opaque = bool(self.stack and self.stack[-1][1])
        mention = not opaque and 'mention-image-token' in attrs.get('class', '').split()
        block = not opaque and not mention and tag in _BLOCKS
        if mention:
            kind = attrs.get('data-kind') or 'image'
            part = {'url': attrs.get('data-url', ''), 'kind': kind,
                    'name': attrs.get('data-name') or ('音频' if kind == 'audio' else '图片'),
                    'nodeId': attrs.get('data-node-id', ''),
                    'imageIndex': _index({'imageIndex': attrs.get('data-image-index', '')}, 0)}
            if attrs.get('data-asset-id'):
                part['asset_id'] = attrs['data-asset-id']
            try:
                part['asset_uris'] = json.loads(attrs.get('data-asset-uris') or '{}') or {}
            except (ValueError, RecursionError):
                pass
            self.parts.append(part)
        elif not opaque and tag == 'br':
            self.handle_data('\n')
        elif block and self.parts and self.parts[-1].get('text', '').rstrip('\n') == self.parts[-1].get('text'):
            if self.parts[-1].get('text'):
                self.handle_data('\n')
        if tag not in _VOID:
            self.stack.append((tag, opaque or mention or tag == 'template', block))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        match = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if match is not None:
            while len(self.stack) > match:
                _, _, block = self.stack.pop()
                if block:
                    self.handle_data('\n')

    def handle_data(self, text):
        if text and not (self.stack and self.stack[-1][1]):
            self.parts.append({'text': text})


def prompt_parts(html):
    return _PromptParts(html).parts


def _input_key(item):
    node, index = _str(item.get('nodeId')), item.get('imageIndex')
    return f'{node}|{_index(item, 0)}' if node and (index is None or math.isfinite(_number(index, math.nan))) else f'url|{_url(item)}'


def _unique(items, managed, limit=None, *, defaults=True):
    urls, bare_urls, assets = set(), set(), set()
    refs = []
    for index, item in enumerate(items):
        url, asset = _url(item), _asset(item) if managed else ''
        if not url:
            continue
        duplicate = (asset in assets or url in bare_urls) if asset else url in urls
        urls.add(url)
        if asset:
            assets.add(asset)
        elif not duplicate:
            bare_urls.add(url)
        if not duplicate:
            refs.append({**item, 'name': _str(item.get('name')) or f'图{len(refs) + 1}',
                         'imageIndex': _index(item, index)} if defaults else item)
            if limit and len(refs) >= limit:
                break
    return refs


def outpaint_geometry(node):
    size = node.get('outpaintSize')
    if not isinstance(size, dict):
        return ''
    width, height = (math.floor(_number(size.get(key)) + .5) for key in ('width', 'height'))
    if min(width, height) < 1:
        return ''
    ratio = _str(size.get('aspectRatio')).strip()
    if re.fullmatch(r'\d+\s*:\s*\d+', ratio):
        return re.sub(r'\s+', '', ratio)
    divisor = math.gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


class ReferenceGraph:
    def __init__(self, canvas, project_id, media=None):
        self.project_id = project_id
        self.prefix = f'/api/project-library/{quote(project_id, safe="")}/media/'
        self.nodes = {_str(node.get('id')): node for node in _objects(canvas.get('nodes')) if _str(node.get('id'))}
        self.edges = _objects(canvas.get('connections'))
        self.legacy = 'connections' not in canvas or canvas.get('legacy_inputs') is True
        self.media = media or {}

    def hydrate(self, item):
        if not _url(item) and (asset := _asset(item)):
            return {**item, 'url': self.prefix + quote(asset, safe='')}
        return item

    def _inputs(self, node, workflow=False):
        target = node.get('id')
        sources, managed = {}, set()
        for edge in self.edges:
            source = _str(edge.get('from'))
            if edge.get('to') != target or not source:
                continue
            if managed_edge(edge, target):
                managed.add(source)
            elif edge.get('kind') == 'input' or (workflow and (edge.get('kind') or 'flow') == 'flow'):
                sources[source] = None
        if self.legacy:
            sources.update(dict.fromkeys(source for source in _strings(node.get('inputNodeIds')) if source not in managed))
        return [self.nodes[source] for source in sources if source in self.nodes]

    def _members(self, node):
        return [self.nodes[key] for key in dict.fromkeys(_strings(node.get('items')))
                if key in self.nodes and key != node.get('id') and self.nodes[key].get('type') != 'smart-group']

    def _images(self, node):
        def own_images(source):
            images = source.get('images')
            if source.get('type') == 'smart-container' and not images and isinstance(source.get('inputImage'), dict):
                images = [source['inputImage']]
            for index, image in enumerate(images if isinstance(images, list) else []):
                if not isinstance(image, dict):
                    continue
                item = self.hydrate(image)
                local = next((_str(item.get(key)) for key in ('originalLocalUrl', 'localUrl', 'sourceUrl', 'local_url', 'source_url', 'url')
                              if _str(item.get(key)) and not re.match(r'https?://', item[key], re.I)), '')
                if local or _url(item):
                    yield {**item, 'url': local or _url(item), 'nodeId': source['id'], 'imageIndex': index}

        def reading_order(a, b):
            dy = _number(a.get('y')) - _number(b.get('y'))
            return dy if abs(dy) > 24 else _number(a.get('x')) - _number(b.get('x'))

        items = list(own_images(node))
        if node.get('type') == 'smart-group':
            for member in sorted(self._members(node), key=cmp_to_key(reading_order)):
                if member.get('type') in ('smart-image', 'smart-container', 'image', None):
                    items.extend(own_images(member))
        return items

    def _managed(self, node):
        edges = managed_edges({'connections': self.edges}, node.get('id'))
        selected = {}
        sources = {}
        for edge in edges:
            asset, source_id = _asset(edge), _str(edge.get('from'))
            if edge.get('enabled') is not True or not asset or asset in selected or source_id not in self.nodes:
                continue
            if source_id not in sources:
                sources[source_id] = {img['asset_id']: img for img in reversed(self._images(self.nodes[source_id])) if _asset(img)}
            item = sources[source_id].get(asset) or self.media.get(asset) or {}
            selected[asset] = self.hydrate({**item, 'asset_id': asset, 'agentRefRole': _str(edge.get('role')) or 'general',
                                            'agentRefEdgeId': edge.get('id')})
        return bool(edges), list(selected.values())

    def _outputs(self, node, visiting, ctx, *, loop_input=False):
        ident = node['id']
        if node.get('type') == 'smart-loop':
            if not node.get('imageInput') or ident in visiting:
                return []
            managed, refs = self._managed(node)
            for source in self._inputs(node):
                refs.extend(self._outputs(source, visiting | {ident}, ctx, loop_input=True))
            if managed:
                # Loop slicing happens after asset-aware dedup but before the target cap.
                refs = _unique(refs, True, defaults=False)
            start = max(1, int(_number(ctx.get('index') or node.get('loopStart') or 1))) - 1
            size = min(100, max(1, int(_number(node.get('imageBatchSize') or 1))))
            label = 'Loop image' if ctx.get('english') else '循环图片'
            return [{**img, 'name': _str(img.get('name')) or f'{label} {start + i + 1}'} for i, img in enumerate(refs[start:start + size])]
        round_outputs = ctx.get('roundOutputs')
        round_outputs = round_outputs if isinstance(round_outputs, dict) else {}
        if not loop_input and node.get('type') != 'smart-group' and ident in round_outputs:
            return [image for image in _objects(round_outputs[ident]) if _url(image)]
        return self._images(node)

    def _prompt_items(self, node, visiting, ctx):
        kind = node.get('type')
        if kind == 'smart-prompt':
            text = _str(node.get('text')).strip()
            split = [s.strip() for s in text.split(_str(node.get('promptSeparator')) or ';') if s.strip()]
            return split if node.get('promptSplitEnabled') is True and len(split) > 1 else [text] if text else []
        if kind == 'smart-group':
            return [text for member in self._members(node) for text in self._prompt_items(member, visiting, ctx)]
        if kind != 'smart-loop' or not node.get('showPrompt') or node['id'] in visiting:
            return []
        items = [text for source in self._inputs(node) for text in self._prompt_items(source, visiting | {node['id']}, ctx)]
        fields = node.get('variablePrompts')
        if isinstance(fields, list) and fields:
            local = [part.strip() for part in _strings(fields) if part.strip()]
        else:
            text = _str(node.get('variablePrompt')).strip()
            local = [part.strip() for part in re.split(r'\s*(?:^|\s)\d+\s*[.、)）．]\s+', text) if part.strip()]
            if len(local) < 2:
                local = [part.strip() for part in text.splitlines() if part.strip()]
        index = max(1, int(_number(ctx.get('index') or node.get('loopStart') or 1)))
        count = max(1, int(_number(ctx.get('total') or min(100, _number(node.get('count') or 1)))))
        upstream = items[(index - 1) % len(items)] if items else ''
        own = local[(index - 1) % len(local)] if local else ''
        if upstream and own in ('现在生成第《计数》张卖点图片', 'Generate selling-point image 《计数》'):
            own = ''
        text = '\n\n'.join(filter(None, (upstream, own)))
        for label, value in (('计数', index), ('总数', count), ('进度', f'{index}/{count}')):
            text = text.replace(f'《{label}》', str(value)).replace(f'[{label}]', str(value))
        if ctx.get('english'):
            text = text.replace('[Count]', str(index))
        return [text.strip()] if text.strip() else []

    def _prefix_prompt(self, node, body, ctx):
        group = '\n\n'.join(self._prompt_items(node, set(), ctx)) if node.get('type') == 'smart-group' else ''
        inputs = self._inputs(node) + [self.nodes[key] for key in _strings(ctx.get('relayPromptNodeIds')) if key in self.nodes]
        snippets = ['\n\n'.join(self._prompt_items(source, set(), ctx)).strip() for source in inputs]
        upstream = '\n\n'.join(dict.fromkeys(filter(None, snippets)))
        return '\n\n'.join(filter(None, (group, upstream, body)))

    def _saved_parts(self, node):
        html = _str(node.get('promptDraftHtml'))
        if 'mention-image-token' in html:
            return prompt_parts(html)
        draft, run = _str(node.get('promptDraftText')), _str(node.get('runPrompt'))
        text = (draft or run) if html else (run or draft)
        refs = sorted((self.hydrate(ref) for ref in _objects(node.get('runPromptRefs'))),
                      key=lambda ref: -len(_str(ref.get('name'))))
        names = [(f'@{ref["name"]}', ref) for ref in refs if _url(ref) and _str(ref.get('name'))]
        if text and names and '@' in text:
            parts = []
            pos = 0
            for match in re.finditer('|'.join(re.escape(name) for name, _ in names), text):
                ref = next(ref for name, ref in names if name == match.group())
                parts.extend(({'text': text[pos:match.start()]}, {
                    'url': _url(ref), 'name': _str(ref.get('alias')) or ref['name'], 'kind': _kind(ref),
                    'nodeId': _str(ref.get('nodeId')), 'imageIndex': _index(ref, 0), 'asset_uris': ref.get('asset_uris') or {},
                }))
                pos = match.end()
            if parts:
                return [*parts, {'text': text[pos:]}]
        if html:
            return prompt_parts(html)
        if 'promptDraftHtml' in node and not draft and not run:
            return []  # explicitly cleared in the browser
        return [{'text': text or _str(node.get('prompt')) or _str(node.get('text'))}]

    def resolve(self, target, *, html=None, prompt_override=None, ctx=None, override_source=None, cascade_fallback=None):
        node = self.nodes[target]
        ctx = ctx or {}
        managed, linked = self._managed(node)
        manual = []
        for img in map(self.hydrate, _objects(node.get('manualInputRefs'))):
            if _url(img):
                manual.append({**img, 'kind': _str(img.get('kind')) or _kind(img),
                               'name': _str(img.get('name')) or f'图{len(manual) + 1}', 'imageIndex': _index(img, len(manual))})
        defaults, overrides = [], None
        if source := self.nodes.get(_str(override_source)):
            overrides = self._outputs(source, set(), ctx) or None
        if overrides is None:
            workflow = bool(ctx.get('forceWorkflow')) and node.get('type') in (None, 'smart-image', 'smart-container')
            ordinary = [item for source in self._inputs(node, workflow=workflow)
                        for item in self._outputs(source, {target}, ctx)]
            defaults = [*linked, *ordinary, *manual]
            if cascade_fallback is not None:
                # Cascade supplies past round inputs only if this node has no defaults.
                overrides = _unique(defaults, managed, CANVAS_REFERENCE_MAX) or cascade_fallback or None
            if not managed:
                # Legacy URL-only dedup/cap happened before exclusions.
                defaults = _unique(defaults, False, CANVAS_REFERENCE_MAX)
        if overrides is not None:
            defaults = [*linked, *_objects(overrides), *manual] if managed else _objects(overrides)
        blocked = set(_strings(node.get('blockedInputRefs')))
        eligible = [item for item in defaults if _input_key(item) not in blocked]
        refs = _unique(eligible, managed, CANVAS_REFERENCE_MAX)

        if outpaint_geometry(node):
            base = next((img for img in map(self.hydrate, _objects(node.get('images'))) if _url(img)), None)
            if base and not any(_url(ref) == _url(base) or (managed and _asset(base) and _asset(ref) == _asset(base)) for ref in refs):
                refs[CANVAS_REFERENCE_MAX - 1:] = []
                refs.append({'url': _url(base), 'asset_id': _asset(base), 'name': _str(base.get('name')) or '扩图底图',
                             'kind': _str(base.get('kind')) or 'image'})

        urls, assets = {}, {}
        for number, item in enumerate(refs, 1):
            urls.setdefault(_url(item), number)
            if managed and _asset(item):
                assets[_asset(item)] = number
        if managed:
            for img in eligible:
                if _asset(img) in assets:
                    urls.setdefault(_url(img), assets[_asset(img)])
        parts = ([{'text': prompt_override}] if prompt_override is not None else
                 prompt_parts(html) if html is not None else self._saved_parts(node))
        mentioned, body, display = False, [], []
        for part in parts:
            if 'text' in part:
                display.append(part['text'])
                body.append(part['text'])
                continue
            name = _str(part.get('name')) or '图片'
            display.append(f'@{name}')
            url, asset = _url(part), _asset(part)
            if not url:
                continue
            mentioned = True
            if _input_key(part) in blocked:
                body.append(f'@{name}')
                continue
            by_url = urls.get(url)
            number = assets.get(asset) or by_url
            if managed and asset and asset not in assets and by_url and _asset(refs[by_url - 1]):
                number = None
            if not number:
                if len(refs) >= CANVAS_REFERENCE_MAX:
                    body.append(f'@{name}')
                    continue
                number = len(refs) + 1
                urls.setdefault(url, number)
                if managed and asset:
                    assets[asset] = number
                refs.append(part)
            body.append(f'图{number}')
        body_text = _trim(''.join(body))
        if prompt_override is None:
            body_text = self._prefix_prompt(node, body_text, ctx) or _str(ctx.get('emptyPrompt'))
        numbered = []
        for n, img in enumerate(refs, 1):
            ref = {'url': _url(img), 'name': _str(img.get('name')) or f'图{n}', 'kind': _str(img.get('kind')) or _kind(img),
                   'asset_uris': img.get('asset_uris') or {}, 'role': f'image_{n}'}
            if managed and _asset(img):
                ref['asset_id'] = _asset(img)
            if img.get('agentRefRole'):
                ref['agentRefRole'] = img['agentRefRole']
            numbered.append(ref)
        mentioned = mentioned and bool(refs)
        # Keep the browser's locale without changing reference numbering.
        header, user_need = ('Reference image map:', 'User request:') if ctx.get('english') else ('下面是参考图编号：', '用户需求：')
        mapping = '\n'.join(f'图{n}：{ref["name"]}' for n, ref in enumerate(numbered, 1))
        prompt = f'{header}\n{mapping}\n\n{user_need}\n{body_text}' if mentioned else body_text
        return {'prompt': prompt, 'displayPrompt': _trim(''.join(display)) or body_text, 'refs': numbered, 'mentioned': mentioned}

    def owned_image_ids(self, request):
        """Mirror browser import's fast path, without attempting any import."""
        for ref in request['refs']:
            if _kind(ref) != 'image':
                continue
            url = _url(ref)
            asset = _asset(ref)
            if not asset and url.startswith(self.prefix):
                try:
                    asset = unquote(urlsplit(url).path[len(self.prefix):].split('/')[0])
                except ValueError:
                    asset = ''
            yield (asset if _ASSET.fullmatch(asset) else '', ref.get('agentRefRole'))
