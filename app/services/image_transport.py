"""Image-only HTTPX observation; timestamps describe client yields, never ACK/TTFB."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import io
import inspect
import json
import math
import time

import httpx
from httpx._client import AsyncClient as _OriginalAsyncClient
from httpx._content import ByteStream
from httpx._multipart import DataField, FileField, MultipartStream
from httpx._transports.default import AsyncHTTPTransport as _OriginalHTTPTransport

__all__ = ["ImageTransportClient", "create_image_transport_client", "image_response_json", "record_image_decode"]

_IMAGE_OBSERVATION_SINK = ContextVar('image_observation_sink', default=None)
_IMAGE_OBSERVATION_RESPONSE = ContextVar('image_observation_response', default=None)
_IMAGE_UPLOAD_RETRY_BUDGET = ContextVar('image_upload_retry_budget', default=None)


@contextmanager
def _image_upload_retry_scope():
    # Business retries inherit one mutable budget, including child Agent tasks (#493).
    if _IMAGE_UPLOAD_RETRY_BUDGET.get() is not None:
        yield
        return
    token = _IMAGE_UPLOAD_RETRY_BUDGET.set({'remaining': 2})
    try:
        yield
    finally:
        _IMAGE_UPLOAD_RETRY_BUDGET.reset(token)


@contextmanager
def _capture_image_transport(sink):
    """Task-local observation only; no request identity or provider data enters receipts."""
    sink_token = _IMAGE_OBSERVATION_SINK.set(sink)
    response_token = _IMAGE_OBSERVATION_RESPONSE.set(None)
    try:
        yield
    finally:
        _IMAGE_OBSERVATION_RESPONSE.reset(response_token)
        _IMAGE_OBSERVATION_SINK.reset(sink_token)


def _record_current_image_decode(kind, elapsed_ms, output_bytes):
    record_image_decode(_IMAGE_OBSERVATION_RESPONSE.get(), kind, elapsed_ms, output_bytes)


_PHASES = {"generate", "poll", "download", "probe"}
_RETRY_DELAYS = (1, 3)
_ORIGIN_OPERATIONS = {
    f"{protocol}.{phase}" for protocol in ("http11", "http2")
    for phase in ("send_request_headers", "send_request_body", "receive_response_headers")
}


def _error_class(exc: BaseException) -> str:
    # Exception text and user-defined class names can both contain secrets.
    for cls in (asyncio.CancelledError, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError,
                httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.DecodingError,
                httpx.PoolTimeout, httpx.ProxyError, httpx.HTTPStatusError,
                json.JSONDecodeError, ValueError, OSError, Exception, BaseException):
        if isinstance(exc, cls):
            return cls.__name__
    return "BaseException"


class _Record:
    def __init__(self, publish, phase, attempt, build_ms=None):
        self.started = time.perf_counter()
        self.publish = publish
        self.finished = False
        self.decoding = False
        self.raw_complete = False
        self.origin_body_started = False
        self.connect_failed = False
        self.upload_failed = False
        self.active_phase = "connect"
        self.operations = {}
        self.data = dict(
            event="transport", phase_tag=phase, attempt=attempt,
            request_build_including_serialization_ms=build_ms,
            request_body_yielded_bytes=None, request_write_complete_ms=None,
            response_headers_complete_ms=None, first_raw_body_yield_ms=None,
            first_decoded_body_yield_ms=None, response_raw_entity_bytes=None,
            response_decoded_yielded_bytes=None, raw_body_complete_ms=None,
            body_complete_ms=None, elapsed_ms=None, body_complete=False,
            cancelled=False, failure_phase=None, failure_class=None,
            json_decode_ms=None, base64_decode_ms=None, base64_output_bytes=None,
            image_decode_ms=None, image_output_bytes=None, extract_decode_ms=None,
            extract_output_bytes=None, retry_scheduled=False,
            physical_ttfb_ms=None,
        )

    def ms(self):
        return (time.perf_counter() - self.started) * 1000

    def fail(self, exc, phase=None):
        if self.data["failure_class"] is None:
            self.data["failure_phase"] = phase or self.active_phase
            self.data["failure_class"] = _error_class(exc)
        self.data["cancelled"] |= isinstance(exc, asyncio.CancelledError)

    def finish(self):
        if not self.finished:
            self.finished = True
            self.data["elapsed_ms"] = self.ms()
            self.publish(dict(self.data))

    def update(self, event):
        self.publish(dict(self.data, event=event))

    def trace(self, name, info, tap):
        operation, _, state = name.rpartition(".")
        if operation in {"connection.connect_tcp", "connection.connect_unix_socket",
                         "connection.start_tls", "proxy.start_tls"}:
            if state == "failed" and not self.origin_body_started:
                self.connect_failed = True
            return
        if operation not in _ORIGIN_OPERATIONS:
            return
        if state == "started":
            core_request = info.get("request")
            # CONNECT inherits the origin extensions, but not its body stream.
            origin = getattr(core_request, "stream", None) is tap
            self.operations[operation] = origin
            if origin:
                if operation.endswith("send_request_body"):
                    self.origin_body_started = True
                    self.active_phase = "upload"
                elif operation.endswith("send_request_headers"):
                    self.active_phase = "upload"
                elif operation.endswith("receive_response_headers"):
                    self.active_phase = "headers"
            return
        if not self.operations.get(operation, False):
            return
        if state == "complete" and operation.endswith("send_request_body"):
            self.data["request_write_complete_ms"] = self.ms()
        elif state == "complete" and operation.endswith("receive_response_headers"):
            self.data["response_headers_complete_ms"] = self.ms()
        elif state == "failed" and operation.endswith(("send_request_body", "send_request_headers")):
            self.upload_failed = True


class _UploadTap(httpx.AsyncByteStream):
    def __init__(self, stream, record):
        self.stream, self.record = stream, record

    async def __aiter__(self):
        data = self.record.data
        data["request_body_yielded_bytes"] = 0
        async for chunk in self.stream:
            data["request_body_yielded_bytes"] += len(chunk)
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class _RawTap(httpx.AsyncByteStream):
    def __init__(self, stream, record):
        self.stream, self.record = stream, record

    async def __aiter__(self):
        record = self.record
        record.active_phase = "body"
        record.data["response_raw_entity_bytes"] = 0
        try:
            async for chunk in self.stream:
                if chunk and record.data["first_raw_body_yield_ms"] is None:
                    record.data["first_raw_body_yield_ms"] = record.ms()
                record.data["response_raw_entity_bytes"] += len(chunk)
                yield chunk
            record.raw_complete = True
            record.data["raw_body_complete_ms"] = record.ms()
        except BaseException as exc:
            if not isinstance(exc, GeneratorExit):
                record.fail(exc, "body")
                record.finish()
            raise

    async def aclose(self):
        try:
            await self.stream.aclose()
        except BaseException as exc:
            self.record.fail(exc, "body")
            raise
        finally:
            if not self.record.decoding:
                if self.record.raw_complete:
                    self.record.data["body_complete"] = True
                    self.record.data["body_complete_ms"] = self.record.ms()
                self.record.finish()


def _observe_response(response, record):
    response._image_transport_record = record
    response.stream = _RawTap(response.stream, record)
    original_bytes = response.aiter_bytes

    async def observed_bytes(chunk_size=None):
        # Cached content rereads are not another download or another decoding pass.
        if hasattr(response, "_content"):
            async for chunk in original_bytes(chunk_size=chunk_size):
                yield chunk
            return
        record.decoding = True
        record.data["response_decoded_yielded_bytes"] = 0
        try:
            async for chunk in original_bytes(chunk_size=chunk_size):
                if chunk and record.data["first_decoded_body_yield_ms"] is None:
                    record.data["first_decoded_body_yield_ms"] = record.ms()
                record.data["response_decoded_yielded_bytes"] += len(chunk)
                yield chunk
            record.data["body_complete"] = True
            record.data["body_complete_ms"] = record.ms()
        except BaseException as exc:
            if not isinstance(exc, GeneratorExit):
                record.fail(exc, "body" if isinstance(exc, httpx.TransportError) else "response_decode")
            raise
        finally:
            record.decoding = False
            record.finish()

    response.aiter_bytes = observed_bytes
    if response.is_closed:
        # An eager custom transport may have consumed everything before this seam.
        # Leave unobserved yield/completion phases null rather than inventing them.
        record.finish()


def _replay_check(stream):
    # Unknown generators, progress wrappers and disk files cannot prove byte-identical replay.
    if type(stream) is ByteStream and isinstance(stream._stream, bytes):
        body = stream._stream
        return lambda restore=False: type(stream) is ByteStream and stream._stream == body
    if type(stream) is not MultipartStream:
        return None
    boundary, fields = stream.boundary, tuple(stream.fields)
    checks = []
    files = []
    cursors = []
    for field in fields:
        if type(field) is DataField:
            checks.append((field, "value", field.value))
        elif type(field) is FileField:
            value = field.file
            files.append((field, value))
            if isinstance(value, (bytes, str)):
                checks.append((field, "file", value))
            elif type(value) is io.BytesIO:
                checks.append((value, "getvalue", value.getvalue()))
                cursors.append((value, value.tell()))
            else:
                return None
        else:
            return None
        for attr in ("_headers", "_data"):
            if hasattr(field, attr):
                checks.append((field, attr, getattr(field, attr)))

    def unchanged(restore=False):
        if stream.boundary != boundary or tuple(stream.fields) != fields:
            return False
        if any(field.file is not value for field, value in files):
            return False
        for obj, attr, expected in checks:
            actual = getattr(obj, attr)
            if (actual() if attr == "getvalue" else actual) != expected:
                return False
        if restore:
            for value, cursor in cursors:
                value.seek(cursor)
        return True
    return unchanged


def _declared_length(request):
    # Chunked framing or conflicting/unknown lengths cannot prove an incomplete body.
    if "transfer-encoding" in request.headers:
        return None
    value = request.headers.get("content-length", "")
    return int(value) if value.isascii() and value.isdecimal() else None


class ImageTransportClient(_OriginalAsyncClient):
    """Pinned to HTTPX 0.28/httpcore 1.0 observation seams; no replacement transport."""

    def __init__(self, *, telemetry_sink=None, phase_tag="generate", max_upload_retries=0, **kwargs):
        self.telemetry_records = deque(maxlen=128)
        self._image_sink = telemetry_sink
        self._image_phase = phase_tag if phase_tag in _PHASES else "other"
        self._image_retries = min(2, max(0, int(max_upload_retries)))
        super().__init__(**kwargs)

    def _image_publish(self, record):
        self.telemetry_records.append(record)
        if self._image_sink is not None:
            try:
                self._image_sink(dict(record))
            except BaseException:
                # Telemetry must never turn a successful/failed paid call into another outcome.
                pass

    def build_request(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            request = super().build_request(*args, **kwargs)
        except BaseException as exc:
            record = _Record(self._image_publish, self._image_phase, 1)
            record.data["request_build_including_serialization_ms"] = (time.perf_counter() - started) * 1000
            record.fail(exc, "request_build")
            record.finish()
            raise
        request._image_transport_build_ms = (time.perf_counter() - started) * 1000
        return request

    @asynccontextmanager
    async def stream(self, *args, **kwargs):
        async with super().stream(*args, **kwargs) as response:
            try:
                yield response
            except BaseException as exc:
                record = getattr(response, "_image_transport_record", None)
                if record is not None:
                    record.fail(exc, "body_consumer")
                    if record.finished:
                        record.update("consumer_failed")
                    else:
                        record.finish()
                raise

    async def _send_single_request(self, request):
        if not isinstance(request.stream, httpx.AsyncByteStream):
            record = _Record(self._image_publish, self._image_phase, 1,
                             getattr(request, "_image_transport_build_ms", None))
            try:
                return await super()._send_single_request(request)
            except BaseException as exc:
                record.fail(exc, "request_stream")
                record.finish()
                raise
        original_stream = request.stream
        budget = _IMAGE_UPLOAD_RETRY_BUDGET.get()
        original_trace = request.extensions.get("trace")
        had_trace = "trace" in request.extensions
        try:
            replay = _replay_check(original_stream) if self._image_retries and request.method == "POST" else None
        except Exception:
            # A closed/unsupported body still belongs to HTTPX's original failure path.
            replay = None
        for attempt in range(1, self._image_retries + 2):
            record = _Record(self._image_publish, self._image_phase, attempt,
                             getattr(request, "_image_transport_build_ms", None))
            tap = _UploadTap(original_stream, record)
            request.stream = tap
            trusted = type(self._transport_for_url(request.url)) is _OriginalHTTPTransport

            trace_failed = False

            async def trace(name, info):
                nonlocal trace_failed
                record.trace(name, info, tap)
                if original_trace is not None:
                    try:
                        coro = original_trace(name, info)
                        # httpcore rejects Futures and other awaitables, not just sync callbacks.
                        if not inspect.iscoroutine(coro):
                            raise TypeError(
                                "If you're using an asynchronous interface, "
                                "the callback of the `trace` extension should "
                                "be an asynchronous function rather than a normal function."
                            )
                        await coro
                    except BaseException:
                        trace_failed = True
                        raise

            request.extensions["trace"] = trace
            retry = False
            try:
                response = await super()._send_single_request(request)
                if record.data["response_headers_complete_ms"] is None:
                    record.data["response_headers_complete_ms"] = record.ms()
                _observe_response(response, record)
                return response
            except BaseException as exc:
                record.fail(exc, "upload" if record.upload_failed else None)
                yielded = record.data["request_body_yielded_bytes"]
                length = _declared_length(request)
                incomplete = length is not None and yielded is not None and yielded < length
                # Trace absence is NOT evidence. Only the installed transport can prove a
                # connect failure or that its consumed body upper bound is below Content-Length.
                connect_proof = record.connect_failed and not record.origin_body_started
                partial_proof = incomplete and record.upload_failed
                eligible_error = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                                                  httpx.WriteError, httpx.WriteTimeout))
                # httpcore HTTP/1.1 suppresses WriteError and tries reading error headers.
                if isinstance(exc, (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
                    eligible_error = partial_proof
                if (trusted and not trace_failed and replay is not None and eligible_error
                        and record.data["request_write_complete_ms"] is None
                        and record.data["response_headers_complete_ms"] is None
                        and (connect_proof or partial_proof) and attempt <= self._image_retries
                        and (budget is None or budget['remaining'] > 0)):
                    try:
                        retry = replay()
                    except Exception:
                        retry = False
                if retry and budget is not None:
                    budget['remaining'] -= 1
                record.data["retry_scheduled"] = retry
                record.finish()
                if not retry:
                    # A typed connect error must not let Agent restart this already-owned budget.
                    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                        exc._image_transport_final = True
                    raise
                try:
                    await asyncio.sleep(_RETRY_DELAYS[attempt - 1])
                except BaseException as delay_error:
                    record.data.update(cancelled=isinstance(delay_error, asyncio.CancelledError),
                                       failure_phase="retry_delay", failure_class=_error_class(delay_error))
                    record.update("retry_cancelled")
                    raise
                try:
                    unchanged = replay(restore=True)
                except Exception:
                    unchanged = False
                if not unchanged:
                    record.data["retry_scheduled"] = False
                    record.update("retry_refused")
                    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                        exc._image_transport_final = True
                    raise
            finally:
                # A custom transport may intentionally replace the stream via request.aread().
                if request.stream is tap:
                    request.stream = original_stream
                if had_trace:
                    request.extensions["trace"] = original_trace
                else:
                    request.extensions.pop("trace", None)


def create_image_transport_client(provider=None, *, client_factory=None, telemetry_sink=None,
                                  phase_tag="generate", max_upload_retries=0, **normal_client_kwargs):
    factory = httpx.AsyncClient if client_factory is None else client_factory
    # 旧测试工厂拥有自己的构造合同，不能额外注入埋点参数。
    if factory is not _OriginalAsyncClient:
        return factory(**normal_client_kwargs)
    # #493：代理开关延期；provider 不改变路由、证书环境或调用方显式的下载安全参数。
    if telemetry_sink is None:
        telemetry_sink = _IMAGE_OBSERVATION_SINK.get()
    return ImageTransportClient(telemetry_sink=telemetry_sink, phase_tag=phase_tag,
                                max_upload_retries=max_upload_retries, **normal_client_kwargs)


def image_response_json(response):
    if _IMAGE_OBSERVATION_SINK.get() is not None:
        _IMAGE_OBSERVATION_RESPONSE.set(response)
    record = getattr(response, "_image_transport_record", None)
    started = time.perf_counter()
    try:
        return response.json()
    except BaseException as exc:
        if record is not None:
            record.fail(exc, "json_decode")
        raise
    finally:
        if record is not None:
            record.data["json_decode_ms"] = (time.perf_counter() - started) * 1000
            record.update("json_decode")


def record_image_decode(response, kind, elapsed_ms, output_bytes):
    record = getattr(response, "_image_transport_record", None)
    if record is None or kind not in {"base64", "image", "extract"}:
        return
    if (not isinstance(elapsed_ms, (int, float)) or isinstance(elapsed_ms, bool)
            or not math.isfinite(elapsed_ms) or elapsed_ms < 0
            or not isinstance(output_bytes, int) or isinstance(output_bytes, bool) or output_bytes < 0):
        return
    record.data[f"{kind}_decode_ms"] = float(elapsed_ms)
    record.data[f"{kind}_output_bytes"] = min(output_bytes, 2**63 - 1)
    record.update(f"{kind}_decode")
