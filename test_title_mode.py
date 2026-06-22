import unittest

import processor
from models import Product


class TitleModeTests(unittest.TestCase):
    def setUp(self):
        self._orig_deepseek_chat = processor.deepseek_chat

    def tearDown(self):
        processor.deepseek_chat = self._orig_deepseek_chat

    def test_brand_only_mode_uses_deepseek_to_detect_brand_without_rewriting_title(self):
        calls = []
        processor.deepseek_chat = lambda *args, **kwargs: calls.append((args, kwargs)) or '["ZARA"]'
        prod = Product(title="ZARA Foldable Storage Rack 2pcs")

        title = processor.phase1_title(
            prod,
            [],
            prompts={},
            title_mode="brand_only",
        )

        self.assertEqual(title, "Foldable Storage Rack 2pcs")
        self.assertEqual(len(calls), 1)
        self.assertIn("ZARA Foldable Storage Rack 2pcs", calls[0][0][0])

    def test_brand_only_mode_parses_comma_separated_deepseek_result(self):
        processor.deepseek_chat = lambda *args, **kwargs: "JBL, BOSE"
        prod = Product(title="JBL Portable Speaker Black")

        title = processor.phase1_title(
            prod,
            [],
            prompts={},
            title_mode="brand_only",
        )

        self.assertEqual(title, "Portable Speaker Black")

    def test_brand_only_mode_keeps_original_title_when_no_brand_detected(self):
        processor.deepseek_chat = lambda *args, **kwargs: "[]"
        prod = Product(title="Foldable Storage Rack 2pcs")

        title = processor.phase1_title(
            prod,
            [],
            prompts={},
            title_mode="仅去品牌",
        )

        self.assertEqual(title, "Foldable Storage Rack 2pcs")

    def test_brand_only_mode_limits_title_to_45_chars_at_word_boundary(self):
        processor.deepseek_chat = lambda *args, **kwargs: '["ZARA"]'
        original = "ZARA Premium Foldable Storage Rack Organizer Sparkling Lamp"
        prod = Product(title=original)

        title = processor.phase1_title(
            prod,
            [],
            prompts={},
            title_mode="brand_only",
        )

        expected = "Premium Foldable Storage Rack Organizer"
        self.assertEqual(title, expected)
        self.assertLessEqual(len(title), 45)

    def test_title_limit_falls_back_to_hard_cut_when_no_nearby_boundary(self):
        title = processor._limit_title_length("A" * 60)

        self.assertEqual(title, "A" * 45)


if __name__ == "__main__":
    unittest.main()
