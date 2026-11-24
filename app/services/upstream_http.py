"""#493：上游服务商 HTTP 客户端的直连策略（纯函数层）。

决策：
- 直连 = 对目标 origin 挂一个 proxy=None 的原生 transport，绕开系统代理
  （trust_env 的环境代理发现与用户显式 proxy 参数都不能把这条路径拽回代理）。
- 归属优先：调用方显式传了 transport / mounts / proxy，策略一律不注入不覆盖
  （pinned 下载与 MockTransport 测试保持原样）。
- 自定义 client 工厂直通：工厂只认原 kwargs，直连策略只在纯 httpx 构造路径生效。
约束：本模块不得导入 main / app.services.providers（避免循环与副作用）。
"""
from __future__ import annotations

from typing import Any, Iterable

import httpx


def _origins(target: str | Iterable[str]) -> list[str]:
    targets = [target] if isinstance(target, str) else list(target)
    origins: list[str] = []
    for url in targets:
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            continue
        if parsed.port not in (None, (443 if parsed.scheme == 'https' else 80)):
            origin = f"{parsed.scheme}://{parsed.host}:{parsed.port}"
        else:
            origin = f"{parsed.scheme}://{parsed.host}"
        if origin not in origins:
            origins.append(origin)
    return origins


def upstream_client_options(target, *, direct: bool, mode: str, **client_kwargs: Any) -> dict[str, Any]:
    """返回给 httpx 构造器的 kwargs；direct 时对目标 origin 注入 proxy=None transport。

    mode: 'sync' -> httpx.HTTPTransport, 'async' -> httpx.AsyncHTTPTransport。
    transport/mounts/proxy 由调用方显式提供时原样返回（归属优先）。
    """
    if not direct:
        return dict(client_kwargs)
    if any(key in client_kwargs for key in ("transport", "mounts", "proxy")):
        return dict(client_kwargs)
    origins = _origins(target)
    if not origins:
        return dict(client_kwargs)
    transport_class = httpx.AsyncHTTPTransport if mode == "async" else httpx.HTTPTransport
    transport_kwargs: dict[str, Any] = {"proxy": None}
    for key in ("verify", "cert", "http2", "trust_env"):
        if key in client_kwargs:
            transport_kwargs[key] = client_kwargs[key]
    options = dict(client_kwargs)
    options["mounts"] = {origin: transport_class(**transport_kwargs) for origin in origins}
    return options


def create_upstream_async_client(target, *, direct: bool, client_factory=None, **client_kwargs: Any):
    if client_factory is not None:
        return client_factory(**client_kwargs)
    return httpx.AsyncClient(**upstream_client_options(target, direct=direct, mode="async", **client_kwargs))


def create_upstream_client(target, *, direct: bool, client_factory=None, **client_kwargs: Any):
    if client_factory is not None:
        return client_factory(**client_kwargs)
    return httpx.Client(**upstream_client_options(target, direct=direct, mode="sync", **client_kwargs))
