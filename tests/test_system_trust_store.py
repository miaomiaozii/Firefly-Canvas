"""系统证书信任库注入单元测试（企业代理自签证书会让内置根证书库握手失败）。

覆盖：
  1. 注入后 ssl.SSLContext 换成 truststore 的实现（HTTPS 改走系统信任库）。
  2. 重复调用幂等（不会二次打补丁）。
  3. 缺少依赖时降级返回 False，不抛异常。
  4. main.py 在装配 app 之前完成注入（否则启动早期请求仍会证书失败）。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

from app.services.system_trust import enable_system_trust_store


class SystemTrustStoreTests(unittest.TestCase):
    def test_enable_switches_ssl_to_system_store(self):
        import ssl

        import truststore

        already_injected = ssl.SSLContext is truststore.SSLContext
        changed = enable_system_trust_store()
        self.assertEqual(changed, not already_injected)
        self.assertIs(ssl.SSLContext, truststore.SSLContext)
        self.assertIsInstance(ssl._create_default_https_context(), truststore.SSLContext)

    def test_second_call_is_idempotent(self):
        enable_system_trust_store()
        self.assertFalse(enable_system_trust_store())

    def test_missing_dependency_degrades_without_raising(self):
        original = sys.modules.get('truststore')
        sys.modules['truststore'] = None  # 让 import truststore 抛 ImportError
        try:
            self.assertFalse(enable_system_trust_store())
        finally:
            if original is None:
                del sys.modules['truststore']
            else:
                sys.modules['truststore'] = original

    def test_server_entry_enables_trust_store_before_app_assembly(self):
        source = pathlib.Path('main.py').read_text(encoding='utf-8')
        call = source.index('enable_system_trust_store()')
        self.assertIn('from app.services.system_trust import enable_system_trust_store', source)
        self.assertLess(call, source.index('app = FastAPI()'))


if __name__ == '__main__':
    unittest.main()
