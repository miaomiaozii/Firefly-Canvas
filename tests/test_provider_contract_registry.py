import importlib
import unittest
from datetime import date
from pathlib import Path

from app.core import REMOVED_PROVIDER_IDS, SUPPORTED_IMAGE_REQUEST_MODES, SUPPORTED_PROVIDER_PROTOCOLS
from tests.provider_contract import load_provider_contract


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "provider_contracts"


class ProviderContractRegistryTests(unittest.TestCase):
    def test_every_supported_image_request_family_has_one_versioned_contract(self):
        expected = (
            {f"protocol:{protocol}" for protocol in SUPPORTED_PROVIDER_PROTOCOLS}
            | {
                f"openai-mode:{mode}"
                for mode in SUPPORTED_IMAGE_REQUEST_MODES
                if mode != "openai"
            }
            | {
                "runninghub-mode:ai-app",
                "openai-provider:comfly",
                "openai-model:gpt-image-2",
                "openai-model:gemini-image",
                "openai-model:nano-banana",
            }
        )
        owners = {}
        excluded = {}
        for path in sorted(FIXTURE_ROOT.glob("*.json")):
            contract = load_provider_contract(path)
            self.assertEqual(contract["schema_version"], 1, path.name)
            self.assertEqual(date.fromisoformat(contract["retrieved_at"]).isoformat(), contract["retrieved_at"], path.name)
            self.assertTrue(contract["sources"], path.name)
            source_ids = {source["id"] for source in contract["sources"]}
            self.assertEqual(len(source_ids), len(contract["sources"]), path.name)
            for source in contract["sources"]:
                self.assertEqual(
                    date.fromisoformat(source["retrieved_at"]).isoformat(),
                    source["retrieved_at"],
                    source["id"],
                )
                self.assertTrue(source.get("url") or source.get("path"), source["id"])
            for item in contract.get("excluded_families", []):
                key = item["key"]
                self.assertNotIn(key, excluded, key)
                self.assertEqual(item["status"], "removed", key)
                self.assertTrue(item["reason"], key)
                self.assertLessEqual(set(item["source_refs"]), source_ids, key)
                excluded[key] = path.name
            family_contracts = {item["key"]: item for item in contract["contracts"]}
            execution_tests = contract["execution_tests"]
            self.assertEqual(set(execution_tests), set(contract["family_keys"]), path.name)
            for key in contract["family_keys"]:
                self.assertNotIn(key, owners, f"{key} is owned by both {owners.get(key)} and {path.name}")
                owners[key] = path.name
                self.assertIn(key, family_contracts, path.name)
                family_contract = family_contracts[key]
                self.assertTrue(family_contract.get("capability_matrix"), key)
                self.assertTrue(family_contract["capability_matrix"].get("resolutions"), key)
                self.assertTrue(family_contract["capability_matrix"].get("aspect_ratios"), key)
                self.assertTrue(family_contract.get("parameter_mapping"), key)
                self.assertTrue(family_contract.get("final_request_golden"), key)
                self.assertTrue(family_contract.get("source_refs"), key)
                self.assertLessEqual(set(family_contract["source_refs"]), source_ids, key)
                self.assertTrue(family_contract.get("request_shape"), key)
                self.assertTrue(family_contract.get("response_shape"), key)
                self.assertEqual(family_contract["capability_matrix"].get("references"), "ordered-with-duplicates", key)
                self.assertTrue(execution_tests[key], key)
                for identifier in execution_tests[key]:
                    module_name, class_name, method_name = identifier.rsplit(".", 2)
                    test_class = getattr(importlib.import_module(module_name), class_name)
                    self.assertTrue(callable(getattr(test_class, method_name, None)), identifier)

        self.assertEqual(set(owners), expected)
        self.assertEqual(set(excluded), {f"provider-id:{provider_id}" for provider_id in REMOVED_PROVIDER_IDS})
        self.assertFalse(set(owners) & set(excluded))


if __name__ == "__main__":
    unittest.main()
