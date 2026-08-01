import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.services import secret_store, write_guard


class SecretStoreTests(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        plain = "sk-abcdef-123456"
        encrypted = secret_store.encrypt_secret(plain)
        self.assertTrue(encrypted.startswith("dpapi:"))
        self.assertNotIn(plain, encrypted)
        self.assertEqual(secret_store.decrypt_secret(encrypted), plain)

    def test_plain_values_pass_through(self):
        self.assertEqual(secret_store.decrypt_secret("plain-key"), "plain-key")
        self.assertEqual(secret_store.decrypt_secret(None), None)
        self.assertEqual(secret_store.encrypt_secret(""), "")

    def test_env_value_helpers(self):
        plain = "env-secret"
        encrypted = secret_store.encrypt_env_value(plain)
        self.assertTrue(secret_store.is_encrypted(encrypted))
        self.assertEqual(secret_store.decrypt_env_value(encrypted), plain)

    def test_same_user_can_read_back_after_file_roundtrip(self):
        plain = "sk-persist-test"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secrets.env"
            target.write_text(f"KEY={secret_store.encrypt_env_value(plain)}\n", encoding="utf-8")
            line = target.read_text(encoding="utf-8").splitlines()[0]
            stored = line.split("=", 1)[1].strip()
            self.assertTrue(stored.startswith("dpapi:"))
            self.assertEqual(secret_store.decrypt_env_value(stored), plain)

    def test_load_env_file_skips_encrypted_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "API" / ".env"
            env_file.parent.mkdir(parents=True)
            encrypted = secret_store.encrypt_env_value("sk-plain-visible")
            env_file.write_text(f"FIREFLY_DPAPI_SKIP_TEST={encrypted}\nFIREFLY_PLAIN_TEST=visible\n", encoding="utf-8")
            previous = main.API_ENV_FILE
            try:
                main.API_ENV_FILE = str(env_file)
                os.environ.pop("FIREFLY_DPAPI_SKIP_TEST", None)
                os.environ.pop("FIREFLY_PLAIN_TEST", None)
                main.load_env_file()
                self.assertEqual(os.environ.get("FIREFLY_DPAPI_SKIP_TEST", ""), "")
                self.assertEqual(os.environ.get("FIREFLY_PLAIN_TEST", ""), "visible")
            finally:
                main.API_ENV_FILE = previous
                os.environ.pop("FIREFLY_DPAPI_SKIP_TEST", None)
                os.environ.pop("FIREFLY_PLAIN_TEST", None)

    def test_provider_env_key_value_decrypts_polluted_environment(self):
        from app.services import providers as _providers_mod
        from app.services.providers import provider_env_key_value, provider_key_env
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "API" / ".env"
            env_file.parent.mkdir(parents=True)
            plain = "sk-polluted-guard"
            fake_provider = "dpapi-guard-fake"
            env_key = provider_key_env(fake_provider)
            self.assertNotIn("COMFLY", env_key)
            env_file.write_text(f"{env_key}={secret_store.encrypt_env_value(plain)}\n", encoding="utf-8")
            previous = main.API_ENV_FILE
            os.environ.pop(env_key, None)
            try:
                main.API_ENV_FILE = str(env_file)
                with patch.object(_providers_mod._svc, "_main", return_value=str(env_file)):
                    self.assertEqual(provider_env_key_value(fake_provider), plain)
                    os.environ[env_key] = "dpapi:not-a-valid-ciphertext"
                    self.assertEqual(provider_env_key_value(fake_provider), plain)
            finally:
                main.API_ENV_FILE = previous
                os.environ.pop(env_key, None)


class WriteGuardTests(unittest.TestCase):
    def test_guard_blocks_real_config_paths_in_test_mode(self):
        with patch.dict(os.environ, {"FIREFLY_TEST_MODE": "1"}):
            for marker in ("API/.env", "data/api_providers.json", "data/global_settings.json"):
                path = ROOT / marker
                with self.assertRaisesRegex(RuntimeError, "refusing to overwrite real config"):
                    write_guard.ensure_writable_config(str(path))

    def test_guard_allows_temp_paths_in_test_mode(self):
        with patch.dict(os.environ, {"FIREFLY_TEST_MODE": "1"}):
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "API" / ".env"
                write_guard.ensure_writable_config(str(target))

    def test_guard_is_inert_without_test_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            write_guard.ensure_writable_config(str(ROOT / "API" / ".env"))

    def test_update_env_values_refuses_real_env_file_in_test_mode(self):
        previous = main.API_ENV_FILE
        try:
            main.API_ENV_FILE = str(ROOT / "API" / ".env")
            with patch.dict(os.environ, {"FIREFLY_TEST_MODE": "1"}):
                with self.assertRaisesRegex(RuntimeError, "refusing to overwrite real config"):
                    main.update_env_values({"FIREFLY_SHOULD_NEVER_LAND": "x"})
        finally:
            main.API_ENV_FILE = previous

    def test_save_api_providers_refuses_real_file_in_test_mode(self):
        previous = main.API_PROVIDERS_FILE
        try:
            main.API_PROVIDERS_FILE = str(ROOT / "data" / "api_providers.json")
            with patch.dict(os.environ, {"FIREFLY_TEST_MODE": "1"}):
                with self.assertRaisesRegex(RuntimeError, "refusing to overwrite real config"):
                    main.save_api_providers([{"id": "x", "name": "X"}])
        finally:
            main.API_PROVIDERS_FILE = previous


if __name__ == "__main__":
    unittest.main()
