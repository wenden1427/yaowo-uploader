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

    def test_brand_only_mode_collects_subject_in_the_same_deepseek_call(self):
        calls = []
        response = '''{
          "brand_words": ["ZARA"],
          "subject": {
            "sold_object": "wall-mounted storage shelf",
            "sold_object_ko": "벽걸이 수납 선반",
            "sold_object_zh": "壁挂收纳架",
            "buyer_receives": "a metal shelf",
            "primary_function": "storage and support",
            "referenced_objects": ["microwave"],
            "attributes": ["wall-mounted"],
            "evidence": ["Storage Rack"],
            "confidence": 0.97
          }
        }'''
        processor.deepseek_chat = lambda *args, **kwargs: calls.append((args, kwargs)) or response
        prod = Product(
            title="ZARA Wall Mounted Microwave Storage Rack",
            tag="steel, kitchen",
        )

        title = processor.phase1_title(prod, [], prompts={}, title_mode="brand_only")

        self.assertEqual(title, "Wall Mounted Microwave Storage Rack")
        self.assertEqual(len(calls), 1)
        self.assertEqual(prod.subject_profile["sold_object_ko"], "벽걸이 수납 선반")
        self.assertEqual(prod.subject_profile["referenced_objects"], ["microwave"])
        self.assertEqual(prod.subject_profile["confidence"], 0.97)

    def test_ai_rewrite_collects_subject_without_a_second_call(self):
        calls = []
        response = '''{
          "title": "벽걸이 주방 수납 선반",
          "subject": {
            "sold_object": "주방 수납 선반",
            "sold_object_ko": "주방선반",
            "sold_object_zh": "厨房置物架",
            "buyer_receives": "금속 선반",
            "primary_function": "주방 수납",
            "referenced_objects": ["전자레인지"],
            "attributes": ["벽걸이"],
            "evidence": ["전자레인지 선반"],
            "confidence": 0.96
          }
        }'''
        processor.deepseek_chat = lambda *args, **kwargs: calls.append((args, kwargs)) or response
        prod = Product(title="전자레인지 선반", tag="스테인리스강")

        title = processor.phase1_title(
            prod,
            [],
            prompts={"title": "Generate a Korean title for {product_title}. {color_note}"},
            title_mode="ai_rewrite",
        )

        self.assertEqual(title, "벽걸이 주방 수납 선반")
        self.assertEqual(len(calls), 1)
        self.assertEqual(prod.subject_profile["sold_object_zh"], "厨房置物架")
        self.assertEqual(prod.subject_profile["referenced_objects"], ["전자레인지"])


if __name__ == "__main__":
    unittest.main()
