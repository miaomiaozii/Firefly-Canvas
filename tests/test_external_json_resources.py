"""#249 外部 JSON 资源引用加锁测试（引用文件缺失/损坏的回归防护）。

思路同 ComfyArt 的 tests/test_workflows.py：产品代码以「路径常量」引用随仓
发放的 JSON 资源，文件一旦缺失/损坏/改结构，运行期要么是静默降级
（功能悄悄没了，零报错），要么是难定位的异常。本测试把每个引用钉死：

- app/data/model_specs.json（模型能力注册表，SPEC-FIRST 单一事实来源）：
  必须存在、可解析、三大模型表结构完好；load_specs 对损坏文件必须抛
  ValueError（清晰报错），对缺失文件返回空结构（文档化的兜底）。
- static/runninghub/api_providers.json（RunningHub 静态 provider 配置）：
  必须存在、可解析、含 id='runninghub' 条目；load_static_runninghub_provider
  能解析出 provider（损坏时它只 logger.warning 后返回 None——静默降级，
  所以必须在这里提前把守）。
- static/runninghub/models_registry.json 是可选文件（providers.py 先查
  exists），不钉存在性，只钉「若存在则必须可解析」。
"""
import json
import os
import tempfile
import unittest

from app.services import model_specs as model_specs_mod
from app.services.model_specs import load_specs
from app.core import (
    STATIC_RUNNINGHUB_API_PROVIDERS_FILE,
    STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE,
    load_static_runninghub_provider,
)

MODEL_SPECS_PATH = model_specs_mod._SPECS_PATH
RUNNINGHUB_PROVIDERS_PATH = STATIC_RUNNINGHUB_API_PROVIDERS_FILE
RUNNINGHUB_REGISTRY_PATH = STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE


class ExternalJsonResourceGuard(unittest.TestCase):
    """随仓 JSON 资源：存在性 + 可解析 + 关键结构 + 缺失/损坏契约。"""

    def test_model_specs_exists_and_parses(self):
        self.assertTrue(
            os.path.isfile(MODEL_SPECS_PATH),
            f"model_specs.json 缺失：{MODEL_SPECS_PATH}（模型能力注册表被删/改名？）",
        )
        with open(MODEL_SPECS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ("chat_models", "image_models", "video_models"):
            self.assertIn(key, data, f"model_specs.json 缺少 {key}")
            self.assertIsInstance(data[key], dict, f"model_specs.json 的 {key} 必须是 dict")
            self.assertTrue(data[key], f"model_specs.json 的 {key} 不应为空")

    def test_load_specs_corrupt_file_raises_clear_error(self):
        old_path = model_specs_mod._SPECS_PATH
        old_cache = model_specs_mod._specs_cache
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = os.path.join(tmp, "model_specs.json")
            with open(corrupt, "w", encoding="utf-8") as f:
                f.write('{"chat_models": {,')  # 故意损坏
            model_specs_mod._SPECS_PATH = corrupt
            try:
                with self.assertRaises(ValueError) as ctx:
                    load_specs(force_reload=True)
                self.assertIn("Invalid model_specs.json", str(ctx.exception))
            finally:
                model_specs_mod._SPECS_PATH = old_path
                model_specs_mod._specs_cache = old_cache

    def test_load_specs_missing_file_returns_empty_structure(self):
        old_path = model_specs_mod._SPECS_PATH
        old_cache = model_specs_mod._specs_cache
        model_specs_mod._SPECS_PATH = os.path.join(tempfile.gettempdir(), "issue249-no-such-specs.json")
        try:
            specs = load_specs(force_reload=True)
            self.assertEqual(
                specs, {"chat_models": {}, "image_models": {}, "video_models": {}},
                "缺失文件必须返回文档化的空结构（静默降级是约定，钉死别改）",
            )
        finally:
            model_specs_mod._SPECS_PATH = old_path
            model_specs_mod._specs_cache = old_cache

    def test_runninghub_api_providers_exists_and_parses(self):
        self.assertTrue(
            os.path.isfile(RUNNINGHUB_PROVIDERS_PATH),
            f"api_providers.json 缺失：{RUNNINGHUB_PROVIDERS_PATH}（RunningHub 静态配置被删/改名？）",
        )
        with open(RUNNINGHUB_PROVIDERS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        candidates = raw if isinstance(raw, list) else (raw.get("providers") if isinstance(raw, dict) else [])
        self.assertTrue(
            any(
                isinstance(item, dict) and str(item.get("id") or "").strip().lower() == "runninghub"
                for item in candidates or []
            ),
            "api_providers.json 里没有 id='runninghub' 的 provider 条目",
        )

    def test_load_static_runninghub_provider_returns_provider(self):
        # 损坏时该函数只 logger.warning 后返回 None（静默降级）——这里钉住正常运行形状。
        provider = load_static_runninghub_provider()
        self.assertIsNotNone(provider, "load_static_runninghub_provider 解析失败（文件损坏或结构被改）")
        self.assertEqual(str(provider.get("id") or "").lower(), "runninghub")

    def test_runninghub_optional_registry_parses_if_present(self):
        # models_registry.json 是可选文件（app/services/providers.py 先查 os.path.exists），
        # 不钉存在性，只钉「若存在则必须可解析」。
        if os.path.isfile(RUNNINGHUB_REGISTRY_PATH):
            with open(RUNNINGHUB_REGISTRY_PATH, "r", encoding="utf-8") as f:
                json.load(f)


if __name__ == "__main__":
    unittest.main()
