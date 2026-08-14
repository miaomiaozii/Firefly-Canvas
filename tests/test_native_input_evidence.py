import copy
import unittest

from tools.native_input_evidence import has_trusted_selector_owner_click


def operation() -> dict:
    return {
        "action": "click",
        "button": "left",
        "trusted": True,
        "observed_type": "click",
        "observed_release_type": "mouseup",
        "native_owner": "button#run",
        "native_owner_instance": 41,
        "observed_within_owner": True,
        "observed_start_owner_instance": 41,
        "observed_start_owner_connected": True,
        "observed_start_within_owner": True,
        "observed_release_owner_instance": 41,
        "observed_release_owner_connected": True,
        "observed_release_within_owner": True,
        "observed_owner_instance": 41,
        "observed_owner_connected": True,
        "observed_semantic_within_owner": True,
    }


class NativeInputEvidenceTests(unittest.TestCase):
    def test_accepts_one_live_selector_owner_for_the_full_click_chain(self):
        self.assertTrue(has_trusted_selector_owner_click(operation(), button="left"))

    def test_rejects_owner_replacement_even_if_the_summary_flag_is_forged_true(self):
        forged = copy.deepcopy(operation())
        forged["observed_release_owner_instance"] = 42
        forged["observed_within_owner"] = True

        self.assertFalse(has_trusted_selector_owner_click(forged, button="left"))

    def test_rejects_a_disconnected_owner_or_retargeted_event(self):
        disconnected = copy.deepcopy(operation())
        disconnected["observed_owner_connected"] = False
        retargeted = copy.deepcopy(operation())
        retargeted["observed_semantic_within_owner"] = False

        self.assertFalse(has_trusted_selector_owner_click(disconnected, button="left"))
        self.assertFalse(has_trusted_selector_owner_click(retargeted, button="left"))


if __name__ == "__main__":
    unittest.main()
