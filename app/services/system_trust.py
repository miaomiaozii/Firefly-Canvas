"""让 Python 的 HTTPS 使用操作系统证书信任库。

企业网络常用自签证书拦截 TLS。Python 自带的是 certifi 根证书库，不认这些企业
根证书，下载生图结果时会报 `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify
failed: self-signed certificate (_ssl.c:1010)`。注入系统信任库后，操作系统里已
被信任的证书（含企业根证书）都会被接受，无需关闭证书校验。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def enable_system_trust_store() -> bool:
    """切换到系统证书信任库。

    返回是否本次完成了切换：已切换过、或缺少依赖、或平台不支持时返回 False。
    """
    try:
        import truststore
    except ImportError:
        logger.warning(
            "未安装 truststore：HTTPS 仍使用内置根证书库，"
            "企业代理自签证书环境下会出现证书校验失败"
        )
        return False
    import ssl

    if getattr(ssl, "SSLContext", None) is getattr(truststore, "SSLContext", None):
        return False  # 已注入过，避免重复打补丁
    try:
        truststore.inject_into_ssl()
    except (OSError, ValueError):  # pragma: no cover - 少数平台没有系统信任库
        logger.warning("系统证书信任库不可用，继续使用内置根证书库", exc_info=True)
        return False
    logger.info("已启用操作系统证书信任库（企业代理自签证书环境下必需）")
    return True
