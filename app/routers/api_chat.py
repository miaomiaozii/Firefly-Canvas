"""Router: /api/chat domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import time
import base64
import shutil
import uuid
import hashlib
import mimetypes
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import *
from app.core import _xlsx_cell_text, _xlsx_join_text, _xlsx_shared_strings, _xlsx_sheet_paths, _xml_local_name
_from_main_names = ['attachment_embedded_image_data_urls', 'attachment_text_blocks', 'build_chat_text_reply', 'codex_chat_text', 'decide_chat_agent_action', 'gemini_cli_chat_text', 'generate_ai_image', 'get_api_provider', 'image_size_from_reference', 'load_api_providers', 'load_conversation', 'new_conversation', 'now_ms', 'output_file_from_url', 'pick_chat_image_provider', 'reference_to_data_url', 'resolve_chat_provider', 'save_ai_image_to_output', 'save_conversation', 'upstream_message_from_record']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class ChatRequest(BaseModel):
    conversation_id: str = ''
    message: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    system_prompt: str = ''
    model: str = ''
    image_model: str = ''
    image_provider: str = ''
    mode: str = 'chat'
    size: str = '1024x1024'
    quality: str = 'auto'
    reference_images: List[AIReference] = []
    provider: str = 'comfly'
    ms_model: str = ''

def chat_system_prompt(payload):
    prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    return prompt or SYSTEM_PROMPT

def display_title(text):
    title = re.sub('\\s+', ' ', text or '').strip()
    return title[:24] or '新对话'

def text_delta_from_chat_chunk(data):
    choices = data.get('choices') or []
    if not choices:
        return ''
    delta = choices[0].get('delta') or {}
    content = delta.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get('text') or item.get('content') or '')
        return ''.join(parts)
    return str(content) if content else ''

def sse_event(data):
    return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'

def _xml_local_name(tag):
    return str(tag or '').rsplit('}', 1)[-1]

def _xlsx_join_text(node):
    parts = []
    for child in node.iter():
        if _xml_local_name(child.tag) == 't' and child.text:
            parts.append(child.text)
    return ''.join(parts).strip()

def _xlsx_shared_strings(archive):
    try:
        raw = archive.read('xl/sharedStrings.xml')
    except KeyError:
        return []
    root = ET.fromstring(raw)
    values = []
    for node in root:
        if _xml_local_name(node.tag) == 'si':
            values.append(_xlsx_join_text(node))
    return values

def _xlsx_sheet_paths(archive):
    names = set(archive.namelist())
    fallback = [(os.path.basename(name).rsplit('.', 1)[0], name) for name in sorted(names) if re.match('xl/worksheets/sheet\\d+\\.xml$', name)]
    try:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        rel_map = {}
        for rel in rels:
            rid = rel.attrib.get('Id')
            target = rel.attrib.get('Target') or ''
            if not rid or not target:
                continue
            target = target.lstrip('/')
            if not target.startswith('xl/'):
                target = f'xl/{target}'
            rel_map[rid] = target.replace('\\', '/')
        result = []
        for sheet in workbook.iter():
            if _xml_local_name(sheet.tag) != 'sheet':
                continue
            title = sheet.attrib.get('name') or 'Sheet'
            rid = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = rel_map.get(rid, '')
            if target in names:
                result.append((title, target))
        return result or fallback
    except Exception:
        return fallback

def _xlsx_cell_text(cell, shared_strings):
    cell_type = cell.attrib.get('t', '')
    value_node = None
    formula_node = None
    inline_node = None
    for child in cell:
        name = _xml_local_name(child.tag)
        if name == 'v':
            value_node = child
        elif name == 'f':
            formula_node = child
        elif name == 'is':
            inline_node = child
    raw_value = (value_node.text if value_node is not None else '') or ''
    formula = (formula_node.text if formula_node is not None else '') or ''
    if cell_type == 's' and raw_value.isdigit():
        idx = int(raw_value)
        value = shared_strings[idx] if 0 <= idx < len(shared_strings) else raw_value
    elif cell_type == 'inlineStr' and inline_node is not None:
        value = _xlsx_join_text(inline_node)
    elif cell_type == 'b':
        value = 'TRUE' if raw_value == '1' else 'FALSE' if raw_value == '0' else raw_value
    else:
        value = raw_value
    value = str(value or '').strip()
    if formula and value:
        return f'{value} [={formula}]'
    if formula:
        return f'={formula}'
    return value

def read_xlsx_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    parts = []
    used = 0
    with zipfile.ZipFile(path) as archive:
        shared = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_paths(archive)
        media_count = sum((1 for name in archive.namelist() if name.startswith('xl/media/') and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS))
        parts.append(f'Excel 工作簿：{os.path.basename(path)}')
        if media_count:
            parts.append(f'内嵌图片数量：{media_count}（已作为图片参考一并提供给模型）')
        for sheet_index, (sheet_name, sheet_path) in enumerate(sheets[:EXCEL_MAX_SHEETS], start=1):
            try:
                root = ET.fromstring(archive.read(sheet_path))
            except Exception:
                continue
            rows = []
            for row in root.iter():
                if _xml_local_name(row.tag) != 'row':
                    continue
                cells = []
                for cell in row:
                    if _xml_local_name(cell.tag) != 'c':
                        continue
                    ref = cell.attrib.get('r') or ''
                    value = _xlsx_cell_text(cell, shared)
                    if value:
                        cells.append(f'{ref}={value}' if ref else value)
                    if len(cells) >= EXCEL_MAX_COLS_PER_ROW:
                        break
                if cells:
                    row_ref = row.attrib.get('r') or str(len(rows) + 1)
                    rows.append(f'第 {row_ref} 行：' + ' | '.join(cells))
                if len(rows) >= EXCEL_MAX_ROWS_PER_SHEET:
                    break
            if rows:
                section = f'\n工作表 {sheet_index}：{sheet_name}\n' + '\n'.join(rows)
            else:
                section = f'\n工作表 {sheet_index}：{sheet_name}\n（未读取到非空单元格）'
            if used + len(section) > limit:
                remain = max(0, limit - used)
                if remain:
                    parts.append(section[:remain])
                parts.append('\n（Excel 内容较长，已截断）')
                break
            parts.append(section)
            used += len(section)
    return '\n'.join(parts).strip()[:limit]

def xlsx_embedded_image_data_urls(path, max_images=4, max_size=1536):
    urls = []
    try:
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist() if name.startswith('xl/media/') and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS]
            for name in sorted(media)[:max_images]:
                try:
                    raw = archive.read(name)
                    with Image.open(BytesIO(raw)) as img:
                        img.load()
                        if max(img.size) > max_size:
                            img.thumbnail((max_size, max_size), Image.LANCZOS)
                        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                            bg = Image.new('RGB', img.size, (255, 255, 255))
                            bg.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
                            img = bg
                        elif img.mode != 'RGB':
                            img = img.convert('RGB')
                        buf = BytesIO()
                        img.save(buf, format='JPEG', quality=88, optimize=True)
                        encoded = base64.b64encode(buf.getvalue()).decode('ascii')
                        urls.append(f'data:image/jpeg;base64,{encoded}')
                except Exception as exc:
                    print(f'[chat] failed to extract xlsx image {name}: {exc}')
    except Exception as exc:
        print(f'[chat] failed to read xlsx images {path}: {exc}')
    return urls

def attachment_embedded_image_data_urls(refs, max_images=4):
    urls = []
    for ref in refs or []:
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = _app_main.output_file_from_url(ref.get('url', ''))
        if not path or os.path.splitext(path)[1].lower() != '.xlsx':
            continue
        urls.extend(xlsx_embedded_image_data_urls(path, max_images=max(0, max_images - len(urls))))
        if len(urls) >= max_images:
            break
    return urls[:max_images]

def read_text_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    ext = os.path.splitext(path or '')[1].lower()
    if not path or not os.path.isfile(path):
        return ''
    try:
        if ext == '.xlsx':
            return read_xlsx_attachment(path, limit)
        if ext == '.xls':
            return '这是旧版 .xls 二进制 Excel 文件，当前内置解析器暂不支持直接读取内容。请另存为 .xlsx 后重新上传。'
        if ext == '.docx':
            with zipfile.ZipFile(path) as archive:
                raw = archive.read('word/document.xml')
            root = ET.fromstring(raw)
            parts = []
            for node in root.iter():
                if node.tag.endswith('}t') and node.text:
                    parts.append(node.text)
                elif node.tag.endswith('}p'):
                    parts.append('\n')
            return html.unescape(''.join(parts)).strip()[:limit]
        if ext in TEXT_ATTACHMENT_EXTS:
            with open(path, 'rb') as f:
                data = f.read(min(os.path.getsize(path), limit * 4))
            for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
                try:
                    return data.decode(encoding, errors='strict').strip()[:limit]
                except UnicodeDecodeError:
                    continue
            return data.decode('utf-8', errors='replace').strip()[:limit]
    except Exception as exc:
        print(f'[chat] failed to read attachment text {path}: {exc}')
    return ''

def attachment_text_blocks(refs, limit_each=MAX_ATTACHMENT_TEXT_CHARS):
    blocks = []
    for ref in (refs or [])[:CHAT_ATTACHMENT_MAX]:
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = _app_main.output_file_from_url(ref.get('url', ''))
        text = read_text_attachment(path, limit_each) if path else ''
        if not text:
            continue
        name = ref.get('name') or os.path.basename(path)
        blocks.append(f'附件：{name}\n{text}')
    return blocks

def chat_prompt_size_override(message, current_size=''):
    text = str(message or '')
    direct = re.search('(?<!\\d)([1-9]\\d{2,4})\\s*[xX×*]\\s*([1-9]\\d{2,4})(?!\\d)', text)
    if direct:
        width, height = (int(direct.group(1)), int(direct.group(2)))
        if width >= 256 and height >= 256:
            return f'{width}x{height}'
    normalized = text.replace('：', ':').replace('﹕', ':').replace('∶', ':').replace('比', ':').replace('／', '/').replace('/', ':')
    ratio_match = re.search('(?<!\\d)(1|2|3|4|9|16)\\s*:\\s*(1|2|3|4|9|16)(?!\\d)', normalized)
    if not ratio_match:
        return ''
    ratio = f'{int(ratio_match.group(1))}:{int(ratio_match.group(2))}'
    options = CHAT_RATIO_SIZE_OPTIONS.get(ratio)
    if not options:
        return ''
    width, height = parse_size_pair(current_size)
    wants_4k = bool(re.search('(?i)\\b4\\s*k\\b|4K|超清|超高分辨率', text))
    wants_2k = bool(re.search('(?i)\\b2\\s*k\\b|2K|高清|高分辨率', text))
    long_edge = max(width, height)
    if wants_4k or long_edge >= 2400:
        return options[2] if len(options) > 2 else options[-1]
    if wants_2k or long_edge >= 1500:
        return options[1] if len(options) > 1 else options[0]
    return options[0]

def upstream_message_from_record(item):
    role = item.get('role')
    if role not in {'user', 'assistant'} or item.get('type') == 'image':
        return None
    attachments = item.get('attachments') or []
    if attachments and role == 'user':
        text = item.get('content', '')
        blocks = attachment_text_blocks(attachments)
        if blocks:
            text = f'{text}\n\n以下是用户上传附件的可读内容，请在回答时参考：\n\n' + '\n\n---\n\n'.join(blocks)
        content = [{'type': 'text', 'text': text}]
        image_urls = []
        for ref in image_references(attachments[:CHAT_ATTACHMENT_MAX]):
            url = _app_main.reference_to_data_url(ref)
            if url:
                image_urls.append(url)
        image_urls.extend(attachment_embedded_image_data_urls(attachments[:CHAT_ATTACHMENT_MAX], max_images=max(0, CHAT_ATTACHMENT_MAX - len(image_urls))))
        for url in image_urls[:CHAT_ATTACHMENT_MAX]:
            content.append({'type': 'image_url', 'image_url': {'url': url}})
        return {'role': role, 'content': content}
    return {'role': role, 'content': item.get('content', '')}

def latest_chat_image_refs(conversation, limit=1):
    refs = []
    for item in reversed(conversation.get('messages') or []):
        url = item.get('image_url') if isinstance(item, dict) else ''
        if url:
            refs.append({'url': url, 'name': item.get('content') or '上一张图片', 'role': 'source'})
        if len(refs) >= limit:
            break
    return refs

def image_size_from_reference(ref):
    path = _app_main.output_file_from_url(ref)
    if not path:
        return ''
    try:
        with Image.open(path) as img:
            width, height = img.size
        if width > 0 and height > 0:
            return f'{width}x{height}'
    except Exception as exc:
        print(f'[chat-agent] failed to read reference image size: {exc}')
    return ''

def chat_requested_image_count(message):
    text = str(message or '')
    match = re.search('(?<!\\d)([1-4])\\s*(?:张|幅|个|组|套)(?!\\d)', text)
    if match:
        return max(1, min(4, int(match.group(1))))
    match = re.search('([一二两俩三四])\\s*(?:张|幅|个|组|套)', text)
    if match:
        return max(1, min(4, CN_NUMERAL_MAP.get(match.group(1), 1)))
    return 1

def chat_split_parallel_prompts(prompt, count):
    text = str(prompt or '').strip()
    if count <= 1:
        return [text]
    noun_match = re.search('(.+?)(?:的)?(海报|头像|壁纸|插画|照片|图片|图像)\\s*$', text)
    if not noun_match:
        return [text] * count
    prefix = noun_match.group(1).strip()
    suffix = noun_match.group(2)
    prefix = re.sub('(?:再)?(?:生成|画|绘制|制作|创建)\\s*[1-4一二两俩三四]?\\s*(?:张|幅|个|组|套)?', '', prefix).strip()
    prefix = re.sub('[,，、\\s]+$', '', prefix).strip()
    if not prefix:
        return [text] * count
    candidates = [item.strip(' ，,、') for item in re.split('\\s*(?:和|与|、|，|,|\\+|＋)\\s*', prefix) if item.strip(' ，,、')]
    if len(candidates) < count:
        return [text] * count
    return [f'{item}的{suffix}' for item in candidates[:count]]

def pick_chat_image_provider(provider_id='', fallback_id=''):
    providers = [p for p in _app_main.load_api_providers() if p.get('enabled', True) and (p.get('image_models') or [])]
    for target in (provider_id, fallback_id):
        clean = str(target or '').strip().lower()
        if clean:
            matched = next((p for p in providers if p.get('id') == clean), None)
            if matched:
                return matched
    if providers:
        primary = next((p for p in providers if p.get('primary')), None)
        return primary or providers[0]
    return _app_main.get_api_provider(provider_id or fallback_id or 'comfly')

def heuristic_agent_decision(message, refs, has_previous_image):
    text = str(message or '').strip().lower()
    has_image_word = any((key.lower() in text for key in AGENT_IMAGE_KEYWORDS))
    has_edit_word = any((key.lower() in text for key in AGENT_EDIT_KEYWORDS))
    if refs and (has_edit_word or has_image_word):
        return {'action': 'edit_image', 'prompt': message, 'reply': ''}
    if has_previous_image and has_edit_word:
        return {'action': 'edit_image', 'prompt': message, 'reply': ''}
    if has_image_word and (not has_edit_word):
        return {'action': 'generate_image', 'prompt': message, 'reply': ''}
    return {'action': 'chat', 'prompt': message, 'reply': ''}

def parse_agent_decision(raw_text, message, refs, has_previous_image):
    text = str(raw_text or '').strip()
    data = None
    if text:
        match = re.search('\\{[\\s\\S]*\\}', text)
        candidate = match.group(0) if match else text
        try:
            data = json.loads(candidate)
        except Exception:
            data = None
    heuristic = heuristic_agent_decision(message, refs, has_previous_image)
    if not isinstance(data, dict):
        return heuristic
    action = str(data.get('action') or '').strip()
    if action not in AGENT_ACTIONS:
        action = heuristic['action']
    prompt = str(data.get('prompt') or message).strip() or message
    reply = str(data.get('reply') or '').strip()
    if action == 'edit_image' and (not (refs or has_previous_image)):
        action = 'generate_image' if any((key.lower() in str(message).lower() for key in AGENT_IMAGE_KEYWORDS)) else 'chat'
    return {'action': action, 'prompt': prompt, 'reply': reply}

async def decide_chat_agent_action(payload, conversation, refs):
    has_previous_image = bool(latest_chat_image_refs(conversation, 1))
    fallback = heuristic_agent_decision(payload.message, refs, has_previous_image)
    provider_cfg = _app_main.get_api_provider(payload.provider) if payload.provider not in ('modelscope',) else {}
    if is_codex_provider(provider_cfg):
        fallback['router_model'] = selected_model(payload.model, (provider_cfg.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        return fallback
    if is_gemini_cli_provider(provider_cfg):
        fallback['router_model'] = selected_model(payload.model, (provider_cfg.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        return fallback
    chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    history = conversation['messages'][-MAX_HISTORY_MESSAGES:]
    custom_system_prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    system = '你是图片创作聊天 Agent 的意图路由器。只返回 JSON，不要 Markdown。\naction 只能是 chat、generate_image、edit_image。\nchat: 普通问答或不需要调用图片工具。\ngenerate_image: 用户要求生成、绘制、创建新图片。\nedit_image: 用户要求修改参考图、上一张图、刚才生成的图，或上传了参考图并要求基于它变化。\nprompt 是交给生图/改图工具的完整中文提示词；普通聊天时也填用户原话。\nreply 是可选的短状态文本。'
    upstream_messages = [{'role': 'system', 'content': system}]
    for item in history[-10:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    upstream_messages.append({'role': 'user', 'content': f"""当前用户输入：{payload.message}\n用户设置的系统提示词：{custom_system_prompt or '无'}\n本次上传参考图数量：{len(refs)}\n对话中是否已有上一张生成图：{('是' if has_previous_image else '否')}\n请返回 JSON，例如 {{"action":"generate_image","prompt":"...","reply":"..."}}"""})
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': model, 'messages': upstream_messages}
            if is_apimart_provider(provider_cfg):
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
            decision = parse_agent_decision(text_from_chat_response(raw), payload.message, refs, has_previous_image)
            decision['router_model'] = model
            return decision
    except Exception as exc:
        print(f'[chat-agent] intent router fallback: {exc}')
        fallback['router_model'] = model
        return fallback

async def build_chat_text_reply(payload, conversation):
    provider_cfg = _app_main.get_api_provider(payload.provider) if payload.provider not in ('modelscope',) else {}
    if is_codex_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await codex_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
        return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
    if is_gemini_cli_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await gemini_cli_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
        return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
    chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    is_apimart = is_apimart_provider(provider_cfg)
    upstream_messages = [{'role': 'system', 'content': chat_system_prompt(payload)}]
    for item in conversation['messages'][-MAX_HISTORY_MESSAGES:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': model, 'messages': upstream_messages}
            if is_apimart:
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ''
        friendly = friendly_chat_error_detail(body, model, provider_cfg)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f'上游接口错误：{body}') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'请求上游接口失败：{exc}') from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
    return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text_from_chat_response(raw).strip() or '接口返回了空回复。', 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': raw_data.get('usage') if isinstance(raw_data, dict) else None}

@router.post('/api/chat')
async def chat(payload: ChatRequest, request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    conversation = load_conversation(user_id, payload.conversation_id) if payload.conversation_id else new_conversation(user_id, display_title(payload.message))
    if not conversation.get('messages'):
        conversation['title'] = display_title(payload.message)
    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    user_message = {'id': uuid.uuid4().hex, 'role': 'user', 'content': payload.message, 'created_at': _app_main.now_ms(), 'attachments': refs, 'mode': payload.mode}
    conversation['messages'].append(user_message)
    conversation['updated_at'] = _app_main.now_ms()
    save_conversation(user_id, conversation)
    if payload.mode == 'image':
        image_provider_id = payload.provider if payload.provider not in {'modelscope'} else 'comfly'
        provider = _app_main.get_api_provider(image_provider_id)
        default_model = (provider.get('image_models') or [IMAGE_MODEL])[0]
        model = selected_model(payload.image_model or payload.model, default_model)
        image_size = chat_prompt_size_override(payload.message, payload.size) or payload.size
        try:
            image_data, raw = await _app_main.generate_ai_image(payload.message, image_size, payload.quality, model, image_refs, provider['id'])
            local_url = await save_ai_image_to_output(image_data, prefix='chat_')
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ''
            detail = friendly_image_error_detail(text, image_size, model) or f'上游生图接口错误：{text[:300]}'
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            log_net_error(f'对话生图 网络/TLS错误 model={model}', exc)
            raise HTTPException(status_code=502, detail=f'请求上游生图接口失败：{exc}') from exc
        assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'type': 'image', 'content': payload.message, 'image_url': local_url, 'created_at': _app_main.now_ms(), 'model': model, 'size': image_size, 'raw_usage': raw.get('usage') if isinstance(raw, dict) else None}
    else:
        _codex_provider = _app_main.get_api_provider(payload.provider)
        if is_codex_provider(_codex_provider):
            model = selected_model(payload.model, (_codex_provider.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
            payload.model = model
            text, raw = await codex_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
            assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
            conversation['messages'].append(assistant_message)
            conversation['updated_at'] = _app_main.now_ms()
            save_conversation(user_id, conversation)
            return {'conversation': conversation, 'message': assistant_message}
        if is_gemini_cli_provider(_codex_provider):
            model = selected_model(payload.model, (_codex_provider.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
            payload.model = model
            text, raw = await gemini_cli_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
            assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
            conversation['messages'].append(assistant_message)
            conversation['updated_at'] = _app_main.now_ms()
            save_conversation(user_id, conversation)
            return {'conversation': conversation, 'message': assistant_message}
        chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
        _conv_provider = _app_main.get_api_provider(payload.provider) if payload.provider not in ('modelscope',) else {}
        _conv_is_apimart = is_apimart_provider(_conv_provider)
        history = conversation['messages'][-MAX_HISTORY_MESSAGES:]
        upstream_messages = [{'role': 'system', 'content': chat_system_prompt(payload)}]
        for item in history:
            msg = upstream_message_from_record(item)
            if msg:
                upstream_messages.append(msg)
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                conv_req_body = {'model': model, 'messages': upstream_messages}
                if _conv_is_apimart:
                    conv_req_body['stream'] = False
                response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=conv_req_body)
                response.raise_for_status()
                raw = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text or ''
            friendly = friendly_chat_error_detail(body, model, _conv_provider)
            raise HTTPException(status_code=exc.response.status_code, detail=friendly or f'上游接口错误：{body}') from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f'请求上游接口失败：{exc}') from exc
        raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
        assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text_from_chat_response(raw).strip() or '接口返回了空回复。', 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': raw_data.get('usage') if isinstance(raw_data, dict) else None}
    conversation['messages'].append(assistant_message)
    conversation['updated_at'] = _app_main.now_ms()
    save_conversation(user_id, conversation)
    return {'conversation': conversation, 'message': assistant_message}

@router.post('/api/chat/agent')
async def chat_agent(payload: ChatRequest, request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    conversation = load_conversation(user_id, payload.conversation_id) if payload.conversation_id else new_conversation(user_id, display_title(payload.message))
    if not conversation.get('messages'):
        conversation['title'] = display_title(payload.message)
    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    user_message = {'id': uuid.uuid4().hex, 'role': 'user', 'content': payload.message, 'created_at': _app_main.now_ms(), 'attachments': refs, 'mode': 'agent'}
    conversation['messages'].append(user_message)
    conversation['updated_at'] = _app_main.now_ms()
    save_conversation(user_id, conversation)
    decision = await decide_chat_agent_action(payload, conversation, image_refs)
    action = decision.get('action') or 'chat'
    tool_refs = image_refs[:]
    inherited_size = ''
    if action == 'edit_image' and (not tool_refs):
        tool_refs = latest_chat_image_refs(conversation, 1)
        inherited_size = image_size_from_reference(tool_refs[0]) if tool_refs else ''
    if action == 'edit_image' and (not tool_refs):
        action = 'generate_image'
    if action in {'generate_image', 'edit_image'}:
        image_provider = pick_chat_image_provider(payload.image_provider or payload.provider, payload.provider)
        default_model = (image_provider.get('image_models') or [IMAGE_MODEL])[0]
        model = selected_model(payload.image_model or default_model, default_model)
        prompt = decision.get('prompt') or payload.message
        prompt_size = chat_prompt_size_override(payload.message, payload.size) or chat_prompt_size_override(prompt, payload.size)
        image_size = snap_size_to_multiple(prompt_size or inherited_size or payload.size, 16)
        requested_count = 1 if action == 'edit_image' else chat_requested_image_count(payload.message)
        prompts = chat_split_parallel_prompts(prompt, requested_count)
        local_urls = []
        raw_items = []
        try:
            for item_prompt in prompts:
                image_data, raw = await _app_main.generate_ai_image(item_prompt, image_size, payload.quality, model, tool_refs, image_provider['id'])
                local_urls.append(await save_ai_image_to_output(image_data, prefix='chat_'))
                raw_items.append(raw)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ''
            detail = friendly_image_error_detail(text, image_size, model) or f'上游生图接口错误：{text[:300]}'
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            log_net_error(f'对话生图 网络/TLS错误 model={model}', exc)
            raise HTTPException(status_code=502, detail=f'请求上游生图接口失败：{exc}') from exc
        local_url = local_urls[0] if local_urls else ''
        assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'type': 'image', 'content': prompt, 'image_url': local_url, 'image_urls': local_urls, 'created_at': _app_main.now_ms(), 'model': model, 'provider': image_provider['id'], 'size': image_size, 'image_count': len(local_urls), 'prompts': prompts, 'agent_action': action, 'agent_reply': decision.get('reply') or '', 'used_references': tool_refs, 'raw_usage': raw_items[0].get('usage') if raw_items and isinstance(raw_items[0], dict) else None}
    else:
        assistant_message = await build_chat_text_reply(payload, conversation)
        assistant_message['agent_action'] = 'chat'
    conversation['messages'].append(assistant_message)
    conversation['updated_at'] = _app_main.now_ms()
    save_conversation(user_id, conversation)
    return {'conversation': conversation, 'message': assistant_message, 'agent': {'action': action, 'decision': decision}}

@router.post('/api/chat/stream')
async def chat_stream(payload: ChatRequest, request: Request, x_user_id: str=Header(default='')):
    if payload.mode == 'image':
        raise HTTPException(status_code=400, detail='图片模式请使用 /api/chat')
    user_id = safe_user_id(x_user_id, request)
    conversation = load_conversation(user_id, payload.conversation_id) if payload.conversation_id else new_conversation(user_id, display_title(payload.message))
    if not conversation.get('messages'):
        conversation['title'] = display_title(payload.message)
    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    user_message = {'id': uuid.uuid4().hex, 'role': 'user', 'content': payload.message, 'created_at': _app_main.now_ms(), 'attachments': refs, 'mode': payload.mode}
    conversation['messages'].append(user_message)
    conversation['updated_at'] = _app_main.now_ms()
    save_conversation(user_id, conversation)
    _codex_provider = _app_main.get_api_provider(payload.provider)
    if is_codex_provider(_codex_provider):
        model = selected_model(payload.model, (_codex_provider.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model

        async def codex_stream():
            yield sse_event({'type': 'meta', 'conversation': conversation})
            try:
                text, raw = await codex_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
            except HTTPException as exc:
                yield sse_event({'type': 'error', 'detail': exc.detail})
                return
            assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
            conversation['messages'].append(assistant_message)
            conversation['updated_at'] = _app_main.now_ms()
            save_conversation(user_id, conversation)
            yield sse_event({'type': 'delta', 'delta': text})
            yield sse_event({'type': 'done', 'conversation': conversation, 'message': assistant_message})
        return StreamingResponse(codex_stream(), media_type='text/event-stream')
    if is_gemini_cli_provider(_codex_provider):
        model = selected_model(payload.model, (_codex_provider.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model

        async def gemini_cli_stream():
            yield sse_event({'type': 'meta', 'conversation': conversation})
            try:
                text, raw = await gemini_cli_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
            except HTTPException as exc:
                yield sse_event({'type': 'error', 'detail': exc.detail})
                return
            assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': None, 'raw': raw}
            conversation['messages'].append(assistant_message)
            conversation['updated_at'] = _app_main.now_ms()
            save_conversation(user_id, conversation)
            yield sse_event({'type': 'delta', 'delta': text})
            yield sse_event({'type': 'done', 'conversation': conversation, 'message': assistant_message})
        return StreamingResponse(gemini_cli_stream(), media_type='text/event-stream')
    chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    _stream_provider = _app_main.get_api_provider(payload.provider) if payload.provider not in ('modelscope',) else {}
    history = conversation['messages'][-MAX_HISTORY_MESSAGES:]
    upstream_messages = [{'role': 'system', 'content': chat_system_prompt(payload)}]
    for item in history:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)

    async def stream():
        content_parts = []
        raw_usage = None
        yield sse_event({'type': 'meta', 'conversation': conversation})
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                async with client.stream('POST', f'{chat_base}/chat/completions', headers=chat_hdrs, json={'model': model, 'messages': upstream_messages, 'stream': True}) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        body = detail.decode('utf-8', errors='ignore')
                        friendly = friendly_chat_error_detail(body, model, _stream_provider)
                        yield sse_event({'type': 'error', 'detail': friendly or f'上游接口错误：{body}'})
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith('data:'):
                            line = line[5:].strip()
                        if line == '[DONE]':
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get('usage'):
                            raw_usage = chunk.get('usage')
                        delta = text_delta_from_chat_chunk(chunk)
                        if delta:
                            content_parts.append(delta)
                            yield sse_event({'type': 'delta', 'delta': delta})
        except httpx.HTTPError as exc:
            log_net_error('对话(流式) 网络/TLS错误', exc)
            yield sse_event({'type': 'error', 'detail': f'请求上游接口失败：{exc}'})
            return
        assistant_message = {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': ''.join(content_parts).strip() or '接口返回了空回复。', 'created_at': _app_main.now_ms(), 'model': model, 'raw_usage': raw_usage}
        conversation['messages'].append(assistant_message)
        conversation['updated_at'] = _app_main.now_ms()
        save_conversation(user_id, conversation)
        yield sse_event({'type': 'done', 'conversation': conversation, 'message': assistant_message})
    return StreamingResponse(stream(), media_type='text/event-stream')