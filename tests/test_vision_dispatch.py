import importlib
import unittest
from unittest.mock import patch

from fastapi import HTTPException


def _conv_mod():
    return importlib.import_module("app.services.conversation")


def _looks_like_vision_chat_model(model):
    lc = str(model or "").strip().lower()
    if not lc:
        return False
    vision_keys = ["vision", "vl-", "-vl-", "internvl", "qvq", "qwen-vl", "doubao-vision", "glm-4v", "minicpm-v"]
    return any(key in lc for key in vision_keys)


def _real_main_call(name, *args, **kwargs):
    if name == "looks_like_vision_chat_model":
        return _looks_like_vision_chat_model
    if _real_main_call.mod is None:
        import main as main_mod

        _real_main_call.mod = main_mod
    if args or kwargs:
        return getattr(_real_main_call.mod, name)(*args, **kwargs)
    return getattr(_real_main_call.mod, name)


def _load_real_main():
    import main as main_mod

    _real_main_call.mod = main_mod
    return main_mod


_load_real_main()
_real_main_call.mod = None


def _patch_svc_main(provider=None):
    def side_effect(name, *args, **kwargs):
        if name in ("get_api_provider", "get_api_provider_exact"):
            return lambda _pid: provider if provider is not None else {}
        if name == "api_headers":
            return lambda **kw: {"Authorization": "Bearer test-key", "Content-Type": "application/json"}
        return _real_main_call(name)

    return patch("app.services.conversation._svc._main", side_effect=side_effect)

class VisionDispatchTests(unittest.TestCase):
    def _provider(self, chat_models):
        return {
            "id": "p1",
            "name": "p1",
            "protocol": "openai",
            "base_url": "https://x.example.com/v1",
            "chat_models": chat_models,
        }

    def _resolve(self, provider, model="", need_vision=False):
        with _patch_svc_main(provider=provider):
            return _conv_mod().resolve_chat_provider("p1", model, "", need_vision=need_vision)

    def test_explicit_non_vision_model_with_image_is_rejected(self):
        provider = self._provider(["gpt-5.4", "gemini-2.5-pro"])
        with self.assertRaises(HTTPException) as ctx:
            self._resolve(provider, model="gpt-5.4", need_vision=True)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不支持识图", ctx.exception.detail)

    def test_auto_picks_vision_model_when_unspecified(self):
        provider = self._provider(["gpt-5.4", "gemini-2.5-pro", "internvl3-78b"])
        _base, _hdrs, model = self._resolve(provider, need_vision=True)
        self.assertEqual(model, "internvl3-78b")

    def test_no_vision_models_raises(self):
        provider = self._provider(["gpt-5.4", "gpt-4o-mini"])
        with self.assertRaises(HTTPException) as ctx:
            self._resolve(provider, need_vision=True)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("没有支持识图", ctx.exception.detail)

    def test_without_image_non_vision_model_is_allowed(self):
        provider = self._provider(["gpt-5.4"])
        _base, _hdrs, model = self._resolve(provider, model="gpt-5.4", need_vision=False)
        self.assertEqual(model, "gpt-5.4")

    def test_vision_model_explicitly_selected_is_allowed(self):
        provider = self._provider(["gpt-5.4", "internvl3-78b"])
        _base, _hdrs, model = self._resolve(provider, model="internvl3-78b", need_vision=True)
        self.assertEqual(model, "internvl3-78b")

    def test_modelscope_with_vision_model_allowed(self):
        with _patch_svc_main(), patch("app.services.conversation._svc.modelscope_api_key", return_value="msk"):
            _base, _hdrs, model = _conv_mod().resolve_chat_provider("modelscope", "", "MiniMax/MiniMax-VL-01", need_vision=True)
            self.assertTrue(_real_main_call("looks_like_vision_chat_model", model))

    def test_modelscope_with_non_vision_rejected(self):
        with _patch_svc_main(), patch("app.services.conversation._svc.modelscope_api_key", return_value="msk"):
            with self.assertRaises(HTTPException) as ctx:
                _conv_mod().resolve_chat_provider("modelscope", "", "MiniMax/MiniMax-M2.7", need_vision=True)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("不支持识图", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
