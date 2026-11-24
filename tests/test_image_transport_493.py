"""Offline installed-HTTPX contracts; no app bootstrap, provider or secret access."""
import asyncio
import gzip
import importlib.util
import io
import json
from pathlib import Path
import ssl
import sys

import httpcore
import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_image_transport_493", Path(__file__).parents[1] / "app/services/image_transport.py"
)
transport = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = transport
_SPEC.loader.exec_module(transport)


def response_bytes(body=b'{"ok":true}', status=200, extra=b""):
    return (b"HTTP/1.1 " + str(status).encode() + b" Test\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\n" + extra + b"\r\n" + body)


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, reads=None, fail_write=None):
        self.reads = list(reads if reads is not None else [response_bytes()])
        self.writes = []
        self.timeouts = []
        self.fail_write = fail_write
        self.closed = 0
        self.tls = []

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)
        self.timeouts.append(timeout)
        if self.fail_write is not None and len(self.writes) == self.fail_write[0]:
            raise self.fail_write[1]

    async def read(self, max_bytes, timeout=None):
        item = self.reads.pop(0) if self.reads else b""
        if isinstance(item, BaseException):
            raise item
        return item

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((ssl_context, server_hostname, timeout))
        return self

    async def aclose(self):
        self.closed += 1

    def get_extra_info(self, info):
        return False if info == "is_readable" else None


class Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def connect_tcp(self, *args, **kwargs):
        self.calls += 1
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def connect_unix_socket(self, *args, **kwargs):
        return await self.connect_tcp(*args, **kwargs)


def backend_for(client, outcomes, url="http://fixture.invalid/upload"):
    backend = Backend(outcomes)
    client._transport_for_url(httpx.URL(url))._pool._network_backend = backend
    return backend


@pytest.mark.parametrize("kind", ["sync", "future"])
def test_trace_invalid_callback_matches_installed_sdk(kind):
    async def exercise(cls):
        calls = []
        stream = Stream()
        def callback(name, info):
            calls.append(name)
            if kind == "future":
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future
            return None

        async with cls(trust_env=False) as client:
            backend = backend_for(client, [stream])
            with pytest.raises(TypeError) as caught:
                await client.post("http://fixture.invalid", content=b"body", extensions={"trace": callback})
            return str(caught.value), calls, backend.calls, stream.writes
    assert asyncio.run(exercise(transport.ImageTransportClient)) == asyncio.run(exercise(httpx.AsyncClient))


def test_existing_trace_failure_never_becomes_upload_retry(monkeypatch):
    async def no_sleep(delay):
        pytest.fail("callback failure is not upload retry evidence")
    monkeypatch.setattr(transport.asyncio, "sleep", no_sleep)

    async def exercise():
        failure = httpx.ConnectError("private callback failure")
        async def callback(name, info):
            if name == "connection.connect_tcp.failed":
                raise failure
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [httpcore.ConnectError()] * 3)
            with pytest.raises(httpx.ConnectError) as caught:
                await client.post("http://fixture.invalid", content=b"body", extensions={"trace": callback})
            assert caught.value is failure
            assert backend.calls == 1
            assert len(client.telemetry_records) == 1
    asyncio.run(exercise())


def test_mock_transport_preserves_body_headers_and_transport_stream_replacement():
    async def exercise(cls):
        captured = []
        async def chunks():
            yield b"first"
            yield b"second"
        async def handler(request):
            captured.append((request.method, (request.url.raw_scheme, request.url.raw_host, request.url.port, request.url.raw_path), request.headers.raw[:], await request.aread()))
            return httpx.Response(200, content=b"ok")
        async with cls(transport=httpx.MockTransport(handler), trust_env=False) as client:
            request = client.build_request("POST", "http://fixture.invalid/path?q=secret", content=chunks(), headers=[("X-Order", "a"), ("X-Order", "b")])
            response = await client.send(request, stream=True)
            await response.aclose()
            assert b"".join([part async for part in request.stream]) == b"firstsecond"
            record = list(getattr(client, "telemetry_records", []))
            if record:
                assert record[-1]["request_body_yielded_bytes"] == 11
                assert record[-1]["request_write_complete_ms"] is None
                assert "secret" not in json.dumps(record)
            return captured, type(request.stream)
    assert asyncio.run(exercise(transport.ImageTransportClient)) == asyncio.run(exercise(httpx.AsyncClient))


def test_sync_request_stream_error_is_unchanged_and_recorded():
    async def exercise(cls):
        async with cls(trust_env=False) as client:
            with pytest.raises(RuntimeError) as caught:
                await client.send(httpx.Request("POST", "http://fixture.invalid", content=iter([b"body"])))
            records = list(getattr(client, "telemetry_records", []))
            if records:
                assert records[-1]["failure_phase"] == "request_stream"
                assert records[-1]["request_body_yielded_bytes"] is None
            return str(caught.value)
    assert asyncio.run(exercise(transport.ImageTransportClient)) == asyncio.run(exercise(httpx.AsyncClient))


def test_installed_versions():
    assert httpx.__version__ == "0.28.1"
    assert httpcore.__version__ == "1.0.9"


@pytest.mark.parametrize("kind", ["json", "multipart"])
@pytest.mark.parametrize("split", [False, True])
def test_wire_identical_and_build_once(kind, split, monkeypatch):
    monkeypatch.setattr("httpx._multipart.os.urandom", lambda count: b"x" * count)
    transport_json_dumps = __import__("httpx._content", fromlist=["json_dumps"]).json_dumps
    serialized = []

    def encode(*args, **kwargs):
        serialized.append(1)
        return transport_json_dumps(*args, **kwargs)

    monkeypatch.setattr("httpx._content.json_dumps", encode)

    async def exercise(cls):
        body = b'{"data":"synthetic"}'
        wire = response_bytes(body)
        reads = [wire] if not split else [wire[:17], wire[17:-4], wire[-4:]]
        stream = Stream(reads)
        traces = []

        async def trace(name, info):
            traces.append(name)

        async with cls(trust_env=False, timeout=httpx.Timeout(13, connect=7, write=11)) as client:
            backend_for(client, [stream])
            kwargs = dict(extensions={"trace": trace, "sni_hostname": "synthetic.invalid"},
                          headers=[("X-Order", "a"), ("X-Order", "b")])
            if kind == "json":
                kwargs["json"] = {"prompt": "synthetic秘密", "n": 1}
            else:
                kwargs.update(data={"prompt": "synthetic秘密", "n": "1"}, files=[
                    ("image[]", ("one.png", io.BytesIO(b"first"), "image/png")),
                    ("image[]", ("two.png", b"second", "image/png")),
                ])
            response = await client.post("http://fixture.invalid/upload", **kwargs)
            assert response.content == body
            metrics = list(getattr(client, "telemetry_records", []))
            before = len(metrics)
            assert await response.aread() == body
            assert b"".join([part async for part in response.aiter_bytes(3)]) == body
            assert len(getattr(client, "telemetry_records", [])) == before
            return stream.writes, stream.timeouts, traces, metrics

    plain = asyncio.run(exercise(httpx.AsyncClient))
    observed = asyncio.run(exercise(transport.ImageTransportClient))
    assert plain[:3] == observed[:3]
    assert len(serialized) == (2 if kind == "json" else 0)
    record = observed[3][-1]
    wire_body = b"".join(observed[0]).split(b"\r\n\r\n", 1)[1]
    assert record["request_body_yielded_bytes"] == len(wire_body)
    assert record["request_build_including_serialization_ms"] >= 0
    assert record["request_write_complete_ms"] is not None
    assert record["response_headers_complete_ms"] >= record["request_write_complete_ms"]
    assert record["first_raw_body_yield_ms"] >= record["response_headers_complete_ms"]
    assert record["response_raw_entity_bytes"] == record["response_decoded_yielded_bytes"] == 20
    assert record["body_complete"] is True
    assert record["physical_ttfb_ms"] is None
    assert "synthetic" not in json.dumps(record)
    assert "秘密" not in json.dumps(record)


def test_gzip_counts_and_decode_helpers():
    async def exercise():
        body = json.dumps({"data": "a" * 1000}).encode()
        compressed = gzip.compress(body)
        stream = Stream([response_bytes(compressed, extra=b"Content-Encoding: gzip\r\n")])
        async with transport.ImageTransportClient(trust_env=False) as client:
            backend_for(client, [stream])
            response = await client.post("http://fixture.invalid", json={})
            assert transport.image_response_json(response) == {"data": "a" * 1000}
            transport.record_image_decode(response, "base64", 2.5, 300)
            transport.record_image_decode(response, "image", 3, 900)
            transport.record_image_decode(response, "SECRET", 1, 3)
            transport.record_image_decode(response, "image", float("nan"), 9)
            record = client.telemetry_records[-1]
            assert record["response_raw_entity_bytes"] == len(compressed)
            assert record["response_decoded_yielded_bytes"] == len(body)
            assert record["json_decode_ms"] >= 0
            assert record["base64_output_bytes"] == 300
            assert record["image_decode_ms"] == 3
            assert len(client.telemetry_records) == 4
    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_body_failure_and_cancel_propagate(cancel):
    failure = asyncio.CancelledError("secret") if cancel else httpcore.ReadError("secret")

    async def exercise():
        stream = Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nabc", failure])
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [stream])
            with pytest.raises(asyncio.CancelledError if cancel else httpx.ReadError) as caught:
                await client.post("http://fixture.invalid", content=b"body")
            if cancel:
                assert caught.value is failure
            record = client.telemetry_records[-1]
            assert record["response_raw_entity_bytes"] == record["response_decoded_yielded_bytes"] == 3
            assert record["body_complete"] is False
            assert record["failure_phase"] == "body"
            assert record["cancelled"] is cancel
            assert record["request_write_complete_ms"] is not None
            assert backend.calls == 1
            assert stream.closed == 1
            assert "secret" not in json.dumps(record)
    asyncio.run(exercise())


def test_chunked_decoded_partial_count_is_yield_lower_bound():
    async def exercise():
        stream = Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nabc", httpcore.ReadError()])
        async with transport.ImageTransportClient(trust_env=False) as client:
            backend_for(client, [stream])
            response = await client.send(client.build_request("GET", "http://fixture.invalid"), stream=True)
            with pytest.raises(httpx.ReadError):
                async for _ in response.aiter_bytes(10):
                    pytest.fail("partial chunk must remain buffered by installed HTTPX")
            await response.aclose()
            assert client.telemetry_records[-1]["response_raw_entity_bytes"] == 3
            assert client.telemetry_records[-1]["response_decoded_yielded_bytes"] == 0
    asyncio.run(exercise())


@pytest.mark.parametrize("read_body", [False, True])
def test_early_close_and_empty_body(read_body):
    async def exercise():
        stream = Stream([response_bytes(b"")])
        async with transport.ImageTransportClient(trust_env=False) as client:
            backend_for(client, [stream])
            response = await client.send(client.build_request("GET", "http://fixture.invalid"), stream=True)
            assert not client.telemetry_records
            if read_body:
                assert await response.aread() == b""
            await response.aclose()
            record = client.telemetry_records[-1]
            assert record["body_complete"] is read_body
            assert record["response_raw_entity_bytes"] == (0 if read_body else None)
            assert record["first_raw_body_yield_ms"] is None
            assert record["first_decoded_body_yield_ms"] is None
    asyncio.run(exercise())


def test_connect_tunnel_is_not_origin_upload_or_origin_headers():
    async def exercise():
        stream = Stream([b"HTTP/1.1 200 Connection established\r\n\r\n", response_bytes()])
        async with transport.ImageTransportClient(trust_env=False, proxy="http://proxy.invalid:8080") as client:
            backend_for(client, [stream], "https://fixture.invalid")
            response = await client.post("https://fixture.invalid", content=b"origin")
            assert response.status_code == 200
            record = client.telemetry_records[-1]
            assert record["request_body_yielded_bytes"] == 6
            assert record["request_write_complete_ms"] is not None
            assert record["response_headers_complete_ms"] > record["request_write_complete_ms"]
            assert stream.writes[0].startswith(b"CONNECT ")
            assert sum(part.startswith(b"POST ") for part in stream.writes) == 1
            assert len(stream.tls) == 1
    asyncio.run(exercise())


def test_connect_tunnel_failure_has_no_origin_upload_or_headers():
    async def exercise():
        stream = Stream([response_bytes(b"", status=407)])
        async with transport.ImageTransportClient(trust_env=False, proxy="http://proxy.invalid:8080", max_upload_retries=2) as client:
            backend = backend_for(client, [stream], "https://fixture.invalid")
            with pytest.raises(httpx.ProxyError):
                await client.post("https://fixture.invalid", content=b"origin")
            record = client.telemetry_records[-1]
            assert record["request_body_yielded_bytes"] is None
            assert record["request_write_complete_ms"] is None
            assert record["response_headers_complete_ms"] is None
            assert backend.calls == 1
    asyncio.run(exercise())


@pytest.mark.parametrize("retries", [0, 1, 2, 9])
def test_connect_retry_limit_delays_and_single_build(retries, monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)

    async def exercise():
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=retries) as client:
            backend = backend_for(client, [httpcore.ConnectError("secret")] * 4)
            with pytest.raises(httpx.ConnectError):
                await client.post("http://fixture.invalid", json={"fixture": 1})
            total = 1 + min(2, retries)
            assert backend.calls == total
            assert len(client.telemetry_records) == total
            assert [item["attempt"] for item in client.telemetry_records] == list(range(1, total + 1))
            assert all(item["request_write_complete_ms"] is None for item in client.telemetry_records)
            assert all(item["request_body_yielded_bytes"] is None for item in client.telemetry_records)
    asyncio.run(exercise())
    assert delays == [1, 3][:min(2, retries)]


@pytest.mark.parametrize("failure", [httpcore.WriteError("secret"), httpcore.WriteTimeout("secret")])
def test_partial_multipart_upload_retries_exact_original_stream(failure, monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)

    async def exercise():
        failed = Stream([httpcore.ReadError("after suppressed write")], fail_write=(8, failure))
        success = Stream()
        plain = Stream()
        file = io.BytesIO(b"original file" * 100)
        file.seek(7)
        initial_cursors = []
        async def trace(name, info):
            if name == "http11.send_request_headers.started":
                initial_cursors.append(file.tell())
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [failed, success])
            request = client.build_request("POST", "http://fixture.invalid", files={"image": ("a.png", file)}, data={"prompt": "synthetic"}, extensions={"trace": trace})
            original = request.stream
            headers = request.headers.raw[:]
            response = await client.send(request)
            assert response.status_code == 200
            assert request.stream is original
            assert request.headers.raw == headers
            assert request.extensions["trace"] is trace
            assert initial_cursors == [7, 7]
            assert backend.calls == 2
            assert client.telemetry_records[0]["request_body_yielded_bytes"] < int(request.headers["content-length"])
            assert client.telemetry_records[0]["request_write_complete_ms"] is None
            assert client.telemetry_records[0]["retry_scheduled"] is True
            assert client.telemetry_records[-1]["request_write_complete_ms"] is not None
            async with httpx.AsyncClient(trust_env=False) as baseline:
                backend_for(baseline, [plain])
                await baseline.send(request)
            assert success.writes == plain.writes
            assert failed.writes == success.writes[:len(failed.writes)]
    asyncio.run(exercise())
    assert delays == [1]


@pytest.mark.parametrize("mode", ["fully_yielded", "final_write", "headers_read", "http429", "http500"])
def test_ambiguous_or_response_failures_never_retry(mode, monkeypatch):
    async def no_sleep(delay):
        pytest.fail("must not schedule upload retry")

    monkeypatch.setattr(transport.asyncio, "sleep", no_sleep)

    async def exercise():
        if mode in {"fully_yielded", "final_write"}:
            stream = Stream([httpcore.ReadError()], fail_write=(2 if mode == "fully_yielded" else 3, httpcore.WriteError()))
        elif mode == "headers_read":
            stream = Stream([httpcore.ReadError()])
        else:
            stream = Stream([response_bytes(status=429 if mode == "http429" else 500)])
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [stream])
            if mode.startswith("http"):
                response = await client.post("http://fixture.invalid", content=b"complete")
                assert response.status_code >= 400
            else:
                with pytest.raises(httpx.ReadError):
                    await client.post("http://fixture.invalid", content=b"complete")
            assert backend.calls == 1
            assert len(client.telemetry_records) == 1
            assert not client.telemetry_records[0]["retry_scheduled"]
    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["GET", "generator", "unknown_transport", "minimax_default"])
def test_unproven_or_nonreplayable_requests_do_not_retry(kind):
    async def chunks():
        yield b"partial"
        yield b"rest"

    async def exercise():
        if kind == "unknown_transport":
            calls = []

            class Unknown(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    calls.append(1)
                    raise httpx.ConnectError("no trusted evidence")

            async with transport.ImageTransportClient(transport=Unknown(), max_upload_retries=2) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.post("http://fixture.invalid", content=b"body")
            assert len(calls) == 1
            return
        kwargs = dict(trust_env=False)
        if kind != "minimax_default":
            kwargs["max_upload_retries"] = 2
        async with transport.create_image_transport_client({"type": "minimax"}, **kwargs) as client:
            backend = backend_for(client, [httpcore.ConnectError()])
            with pytest.raises(httpx.ConnectError):
                if kind == "GET":
                    await client.get("http://fixture.invalid")
                else:
                    await client.post("http://fixture.invalid", content=chunks() if kind == "generator" else b"body")
            assert backend.calls == 1
    asyncio.run(exercise())


def test_legacy_factory_and_monkeypatch_contract(monkeypatch):
    calls = []
    sentinel = object()

    def factory(**kwargs):
        calls.append(kwargs)
        return sentinel

    kwargs = dict(timeout=17, trust_env=True, proxy="http://synthetic.invalid")
    assert transport.create_image_transport_client({"image_bypass_system_proxy": True}, client_factory=factory, telemetry_sink=lambda _: None, max_upload_retries=2, **kwargs) is sentinel
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    assert transport.create_image_transport_client(**kwargs) is sentinel
    assert calls == [kwargs, kwargs]


def test_deferred_proxy_flag_preserves_default_system_proxy_and_explicit_mounts(monkeypatch):
    calls = []

    def proxies():
        calls.append(1)
        return {"http://": "http://proxy.invalid:8080"}

    monkeypatch.setattr("httpx._client.get_environment_proxies", proxies)

    async def exercise():
        async with transport.create_image_transport_client({"image_bypass_system_proxy": False}) as client:
            assert len(calls) == 1
            assert client._transport_for_url(httpx.URL("http://fixture.invalid")) is not client._transport
        explicit = httpx.MockTransport(lambda _: httpx.Response(200))
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}, mounts={"https://pinned.invalid": explicit}) as client:
            assert len(calls) == 2
            assert client._trust_env is True
            assert client._transport_for_url(httpx.URL("http://fixture.invalid")) is not client._transport
            assert client._transport_for_url(httpx.URL("https://pinned.invalid")) is explicit
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}, transport=explicit) as client:
            assert client._transport is explicit
    asyncio.run(exercise())


@pytest.mark.parametrize("value", [True, False, 1, 0, "true", "false", None, [], {}])
def test_no_provider_value_can_enable_deferred_proxy_bypass(value, monkeypatch):
    calls = []
    def proxies():
        calls.append(1)
        return {"http://": "http://proxy.invalid:8080"}
    monkeypatch.setattr("httpx._client.get_environment_proxies", proxies)
    async def exercise():
        async with transport.create_image_transport_client({"image_bypass_system_proxy": value}) as client:
            assert client._trust_env is True
            assert client._transport_for_url(httpx.URL("http://fixture.invalid")) is not client._transport
    asyncio.run(exercise())
    assert calls == [1]


def test_private_explicit_network_policy_is_not_overwritten_by_stale_provider_flag(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    explicit = httpx.MockTransport(lambda _: httpx.Response(200))
    monkeypatch.setattr("httpx._client.get_environment_proxies", lambda: pytest.fail("No proxy discovery for pinned downloads"))

    async def exercise():
        async with transport.create_image_transport_client(
            {"image_bypass_system_proxy": True}, trust_env=False, verify=context,
            transport=explicit, mounts={"https://pinned.invalid": explicit}, follow_redirects=False,
        ) as client:
            assert client._trust_env is False
            assert client.follow_redirects is False
            assert client._transport is explicit
            assert client._transport_for_url(httpx.URL("https://pinned.invalid")) is explicit
        async with transport.create_image_transport_client(
            {"image_bypass_system_proxy": True}, trust_env=False, verify=context,
            proxy="http://private-explicit.invalid:8080",
        ) as client:
            assert client._transport_for_url(httpx.URL("https://fixture.invalid")) is not client._transport
            assert client._transport._pool._ssl_context is context
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    asyncio.run(exercise())


def test_explicit_trust_env_false_still_ignores_certificate_environment(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls = []
    def make_context(*args, **kwargs):
        calls.append(kwargs)
        return context
    monkeypatch.setenv("SSL_CERT_FILE", "synthetic-must-not-be-read")
    monkeypatch.setenv("SSL_CERT_DIR", "synthetic-must-not-be-read")
    monkeypatch.setattr(ssl, "create_default_context", make_context)
    async def exercise():
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}, trust_env=False) as client:
            assert client._trust_env is False
            assert client._transport._pool._ssl_context is context
    asyncio.run(exercise())
    assert len(calls) == 1
    assert "cafile" in calls[0] and calls[0]["cafile"] != "synthetic-must-not-be-read"
    assert "capath" not in calls[0]


@pytest.mark.parametrize("env_name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_default_client_preserves_installed_tls_env_context_despite_stale_flag(env_name, monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls = []

    def make_context(*args, **kwargs):
        calls.append(kwargs)
        return context

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv(env_name, "synthetic-ca-location")
    monkeypatch.setattr("httpx._client.get_environment_proxies", lambda: {})
    monkeypatch.setattr(ssl, "create_default_context", make_context)

    async def exercise():
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}) as client:
            assert client._transport._pool._ssl_context is context
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}, verify=context) as client:
            assert client._transport._pool._ssl_context is context
    asyncio.run(exercise())
    assert calls == [{"cafile" if env_name == "SSL_CERT_FILE" else "capath": "synthetic-ca-location"}]


@pytest.mark.parametrize("stage", ["upload", "retry_delay"])
def test_cancel_upload_and_retry_delay_are_finalized(stage, monkeypatch):
    failure = asyncio.CancelledError("private-cancel")

    async def sleep(delay):
        raise failure

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)

    async def exercise():
        stream = Stream(fail_write=(2, failure))
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [stream if stage == "upload" else httpcore.ConnectError()])
            request = client.build_request("POST", "http://fixture.invalid", content=b"data")
            original = request.stream
            with pytest.raises(asyncio.CancelledError) as caught:
                await client.send(request)
            assert caught.value is failure
            assert request.stream is original
            assert backend.calls == 1
            record = client.telemetry_records[-1]
            assert record["cancelled"]
            assert record["failure_class"] == "CancelledError"
            assert record["failure_phase"] == stage
            assert record["request_write_complete_ms"] is None
            assert "private-cancel" not in json.dumps(record)
    asyncio.run(exercise())


def test_replay_mutation_during_backoff_refuses_second_call(monkeypatch):
    file = io.BytesIO(b"original")

    async def sleep(delay):
        file.seek(0)
        file.write(b"modified")

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)

    async def exercise():
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [httpcore.ConnectError("original failure")])
            with pytest.raises(httpx.ConnectError, match="original failure"):
                await client.post("http://fixture.invalid", files={"image": ("a.png", file)})
            assert backend.calls == 1
            assert client.telemetry_records[-1]["event"] == "retry_refused"
            assert client.telemetry_records[-1]["retry_scheduled"] is False
    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["unknown_length", "progress_wrapper", "disk_file"])
def test_partial_upload_without_exact_replay_or_length_refuses_retry(mode, tmp_path):
    class Progress(io.BytesIO):
        pass

    async def exercise():
        file = Progress(b"file") if mode == "progress_wrapper" else io.BytesIO(b"file")
        if mode == "disk_file":
            path = tmp_path / "owned.png"
            path.write_bytes(b"file")
            file = path.open("rb")
        try:
            async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
                stream = Stream(fail_write=(2, httpcore.WriteTimeout()))
                backend = backend_for(client, [stream])
                request = client.build_request("POST", "http://fixture.invalid", files={"image": ("a.png", file)})
                if mode == "unknown_length":
                    del request.headers["content-length"]
                    request.headers["Transfer-Encoding"] = "chunked"
                with pytest.raises(httpx.WriteTimeout):
                    await client.send(request)
                assert backend.calls == 1
        finally:
            file.close()
    asyncio.run(exercise())


def test_redirects_hooks_cookies_and_streaming_raw_match_baseline():
    async def exercise(cls):
        first = Stream([response_bytes(b"move", 307, b"Location: http://next.invalid/end\r\nSet-Cookie: seen=yes\r\n")])
        second = Stream([b"HTTP/1.1 100 Continue\r\n\r\n" + response_bytes(b"result")])
        hooks = []

        async def request_hook(request):
            hooks.append(("request", request.method, request.headers.raw[:]))

        async def response_hook(response):
            hooks.append(("response", response.status_code))

        async with cls(trust_env=False, follow_redirects=True, event_hooks={"request": [request_hook], "response": [response_hook]}) as client:
            backend = backend_for(client, [first, second])
            request = client.build_request("POST", "http://fixture.invalid", json={"a": 1})
            response = await client.send(request, stream=True)
            assert b"".join([part async for part in response.aiter_raw(2)]) == b"result"
            assert backend.calls == 2
            assert len(response.history) == 1
            return first.writes, second.writes, hooks, list(getattr(client, "telemetry_records", []))
    plain = asyncio.run(exercise(httpx.AsyncClient))
    observed = asyncio.run(exercise(transport.ImageTransportClient))
    assert plain[:3] == observed[:3]
    assert len(observed[3]) == 2
    assert observed[3][-1]["response_raw_entity_bytes"] == 6
    assert observed[3][-1]["response_decoded_yielded_bytes"] is None
    assert observed[3][-1]["body_complete"]


def test_eager_custom_response_is_finalized_without_invented_yields():
    async def exercise():
        async with transport.ImageTransportClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))) as client:
            response = await client.post("http://fixture.invalid", json={})
            assert response.json() == {"ok": True}
            record = client.telemetry_records[-1]
            assert record["response_raw_entity_bytes"] is None
            assert record["response_decoded_yielded_bytes"] is None
            assert record["request_write_complete_ms"] is None
    asyncio.run(exercise())


def test_stream_context_consumer_cancel_preserves_error_and_partial_metrics():
    failure = asyncio.CancelledError("private-consumer")

    async def exercise():
        stream = Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nabc", b"defghi"])
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [stream])
            with pytest.raises(asyncio.CancelledError) as caught:
                async with client.stream("GET", "http://fixture.invalid") as response:
                    async for part in response.aiter_bytes():
                        assert part == b"abc"
                        raise failure
            assert caught.value is failure
            assert backend.calls == 1
            assert stream.closed == 1
            record = client.telemetry_records[-1]
            assert record["cancelled"]
            assert record["body_complete"] is False
            assert record["response_raw_entity_bytes"] == 3
            assert record["response_decoded_yielded_bytes"] == 3
    asyncio.run(exercise())


def test_explicit_truststore_verifier_is_preserved():
    import truststore
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def exercise():
        async with transport.create_image_transport_client({"image_bypass_system_proxy": True}, verify=context) as client:
            assert client._transport._pool._ssl_context is context
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname
    asyncio.run(exercise())


def test_malformed_gzip_propagates_without_retry():
    async def exercise():
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            backend = backend_for(client, [Stream([response_bytes(b"not-gzip", extra=b"Content-Encoding: gzip\r\n")])])
            with pytest.raises(httpx.DecodingError):
                await client.post("http://fixture.invalid", content=b"sent")
            assert backend.calls == 1
            record = client.telemetry_records[-1]
            assert record["body_complete"] is False
            assert record["failure_phase"] == "response_decode"
            assert record["response_raw_entity_bytes"] == 8
    asyncio.run(exercise())


def test_json_failure_build_failure_and_bounded_safe_sink():
    async def exercise():
        received = []

        def broken_sink(record):
            received.append(record)
            raise RuntimeError("private")

        async with transport.ImageTransportClient(trust_env=False, phase_tag="secret-provider", telemetry_sink=broken_sink) as client:
            with pytest.raises(ValueError):
                client.build_request("POST", "http://fixture.invalid", json={"n": float("nan")})
            record = client.telemetry_records[-1]
            assert record["failure_phase"] == "request_build"
            assert record["phase_tag"] == "other"
            backend_for(client, [Stream([response_bytes(b"invalid")])])
            response = await client.post("http://fixture.invalid", content=b"body")
            with pytest.raises(json.JSONDecodeError):
                transport.image_response_json(response)
            assert client.telemetry_records[-1]["failure_phase"] == "json_decode"
            assert client.telemetry_records[-1]["json_decode_ms"] >= 0
            for _ in range(150):
                transport.record_image_decode(response, "image", 1, 2)
            assert len(client.telemetry_records) == 128
            assert all(not isinstance(value, (list, dict)) for value in received[-1].values())
            assert "private" not in json.dumps(received)
            assert "fixture.invalid" not in json.dumps(received)
    asyncio.run(exercise())
