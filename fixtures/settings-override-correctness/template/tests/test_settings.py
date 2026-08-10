import unittest

from src.settings import merge_settings


class MergeSettingsTests(unittest.TestCase):
    def test_missing_and_none_use_defaults(self):
        defaults = {"enabled": True, "limit": 10}

        self.assertEqual(merge_settings(defaults, {}), defaults)
        self.assertEqual(merge_settings(defaults, {"limit": None}), defaults)

    def test_truthy_override_is_preserved(self):
        self.assertEqual(
            merge_settings({"enabled": False}, {"enabled": True}),
            {"enabled": True},
        )


if __name__ == "__main__":
    unittest.main()
