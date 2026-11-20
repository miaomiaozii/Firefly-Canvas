"""A11 policy and the typed boundary between image adapters and Agent tasks."""
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import re
import time
from contextvars import ContextVar

from fastapi import HTTPException
import httpx


AGENT_IMAGE_ATTEMPT = ContextVar('agent_image_attempt', default=False)
AGENT_IMAGE_ACCEPTED = ContextVar('agent_image_accepted', default=False)


def mark_accepted():
    if AGENT_IMAGE_ATTEMPT.get():
        AGENT_IMAGE_ACCEPTED.set(True)


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    hint: str
    retryable: bool = False
    aspect: bool = False

    def public(self):
        return {'code': self.code, 'message': self.message, 'hint': self.hint}


_UNKNOWN = Failure('UNKNOWN', '生成失败', '已标红；请检查生成记录后决定下一步。')


def classify_generation_failure(error_text, http_status, provider, *, error_code=None):
    # Never infer a provider-specific policy from a provider name supplied by a caller.
    text = f'{error_code or ""} {error_text or ""}'.lower().replace('_', ' ')
    def matches(pattern):
        return re.search(pattern, text) is not None

    if http_status == 403 and matches(r'quota|insufficient (?:balance|funds)|saldo|余额不足|额度|配额'):
        return Failure('QUOTA_EXHAUSTED', '服务商额度用尽', '已暂停剩余生成；请充值或更换服务商。')
    if http_status == 429:
        return Failure('RATE_LIMITED', '服务商限流', '请等待服务商的限流窗口结束。', True)
    if matches(r'无可用渠道|no (?:available |eligible )?channels?|model (?:not (?:found|exist)|does not exist|unavailable)|模型(?:不存在|不可用)'):
        return Failure('MODEL_UNAVAILABLE', '模型当前不可用', '只可切换到确认过的备选模型；或请用户在设置里换服务商。', True)
    if matches(r'content (?:policy|filter|reject)|moderation|safety (?:filter|reject|policy)|prompt (?:was )?blocked|安全审核|内容审核|敏感词'):
        return Failure('CONTENT_REJECTED', '服务商拒绝了提示词', '提示词未被改写；请用户检查服务商内容政策。')
    if matches(r'(?:ref(?:erence)? (?:image )?.{0,32}(?:upload|public url).{0,24}(?:fail|error)|upload.{0,20}(?:reference|ref image).{0,24}fail|参考图.{0,30}(?:上传失败|公网.{0,12}(?:失败|不可用))|ref upload failed)'):
        return Failure('REF_UPLOAD_FAILED', '参考图上传失败', '请检查参考图是否可访问。', True)
    aspect = matches(r'aspect[ -]ratio|宽高比|画幅|比例')
    if (aspect or matches(r'\bsize\b|尺寸|分辨率')) and matches(r'unsupported|not support|invalid|不支持|不合法|无效'):
        return Failure('INVALID_PARAMS', '服务商不支持生成参数', '请检查模型支持的比例或尺寸。', aspect, aspect)
    if http_status == 408 or (http_status is not None and 500 <= http_status <= 599) or matches(
            r'connection (?:error|refused|reset|failed)|\btimeout\b|timed? out|超时|连接(?:错误|失败)'):
        return Failure('TRANSIENT', '服务商暂时无法完成请求', '稍后可按相同参数重试。', True)
    return _UNKNOWN


def retry_after_seconds(value, *, now=None):
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        except (TypeError, ValueError):
            pass
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, date.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return 30


class UpstreamImageFailure(HTTPException):
    def __init__(self, detail, *, status=None, code=None, retry_after=None, uncertain=False, sensitive=()):
        super().__init__(status_code=status or 502, detail=detail)
        self.upstream_status = status
        self.error_code = code
        self.retry_after = retry_after
        self.uncertain = uncertain
        self.sensitive = sensitive


def response_failure(response, *, uncertain=False):
    try:
        body = response.json()
    except ValueError:
        body = None
    info = body.get('error', body) if isinstance(body, dict) else None
    code = (info.get('code') or info.get('type')) if isinstance(info, dict) else None
    status = response.status_code
    if 200 <= status < 300 and isinstance(info, dict):
        error_status = info.get('status') or info.get('status_code') or code
        if re.fullmatch(r'[45][0-9]{2}', str(error_status)):
            status = int(error_status)
    # Some gateways include admission/output evidence even in an error response.
    for item in (body, info, body.get('data') if isinstance(body, dict) else None):
        if isinstance(item, dict) and any(item.get(k) for k in (
                'task_id', 'taskId', 'request_id', 'requestId', 'outputs', 'images', 'b64_json')):
            uncertain = True
    try:
        headers = response.request.headers
        sensitive = tuple(value for key, value in headers.items()
                          if re.search(r'authorization|secret|key|token|cookie|signature', key, re.I))
        sensitive += tuple(value.split(' ', 1)[-1] for value in sensitive)
    except (AttributeError, RuntimeError):
        sensitive = ()
    return UpstreamImageFailure(response.text, status=status, code=code, retry_after=response.headers.get('retry-after'),
                                uncertain=uncertain, sensitive=sensitive)


def provider_failure(exc):
    if isinstance(exc, UpstreamImageFailure):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return response_failure(exc.response, uncertain=exc.request.method != 'POST')
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return UpstreamImageFailure('Connection error', code='timeout' if isinstance(exc, httpx.TimeoutException) else None)
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        # Once sending or waiting for a response, lack of ACK is not a rejection.
        return UpstreamImageFailure('Connection error or timeout', uncertain=True)
    return None


_SECRET = re.compile(
    r'(?i)(?:https?://[^\s<>"\']+|(?:bearer|basic)\s+[^\s,;"\']+|'
    r'["\']?(?:[\w-]*(?:key|token|secret|password|authorization|credential|signature|cookie)[\w-]*)["\']?\s*[:=]\s*'
    r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;}]*)|[\w+/=.-]{20,})')


def sensitive_provider_values(provider):
    def values(item):
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for child in item.values():
                yield from values(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                yield from values(child)

    for key, item in (provider or {}).items():
        if re.search(r'key|token|secret|auth|header|credential|password|cookie', key, re.I):
            yield from values(item)
        elif isinstance(item, dict):
            yield from sensitive_provider_values(item)


def describe_failure(exc, provider, *, no_replay=False, sensitive=()):
    upstream = provider_failure(exc)
    if upstream is None:
        return {'failure': _UNKNOWN.public(), 'replay_safe': False, 'automatic_retry': False}
    result = classify_generation_failure(upstream.detail, upstream.upstream_status, provider,
                                         error_code=upstream.error_code)
    raw = str(upstream.detail)
    for value in sorted({s for s in (*sensitive, *upstream.sensitive) if isinstance(s, str) and s},
                        key=len, reverse=True):
        raw = raw.replace(value, '[redacted]')
    raw = _SECRET.sub('[redacted]', raw[:16000])[:500]
    safe = not (no_replay or upstream.uncertain or result.code == 'UNKNOWN')
    fields = {'failure': result.public(), 'details': {'raw': raw}, 'replay_safe': safe,
              'automatic_retry': safe and result.retryable}
    if result.code == 'RATE_LIMITED':
        fields['retry_after_seconds'] = retry_after_seconds(upstream.retry_after)
    if result.aspect:
        fields['adjust_aspect'] = True
    return fields
