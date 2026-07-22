import unittest

import processor
from models import Product
from processor import ensure_upload_category_codes, phase1_category


class CategoryCodeTests(unittest.TestCase):
    def test_ignores_esm_only_category_and_uses_uploadable_candidate(self):
        prod = Product(title="Cat House", tag="Cat House")
        categories = {
            "Pets>Cat House": {
                "esm_code": "esm-only",
                "auction": "",
                "gmarket": "",
            },
            "Pets>Cat": {
                "esm_code": "uploadable",
                "auction": "auction-code",
                "gmarket": "gmarket-code",
            },
        }

        path, esm_code, auction_code, gmarket_code = phase1_category(prod, categories)

        self.assertEqual(path, "Pets>Cat")
        self.assertEqual(esm_code, "uploadable")
        self.assertEqual(auction_code, "auction-code")
        self.assertEqual(gmarket_code, "gmarket-code")

    def test_returns_no_match_when_every_candidate_is_esm_only(self):
        prod = Product(title="Cat House", tag="Cat House")
        categories = {
            "Pets>Cat House": {
                "esm_code": "esm-only",
                "auction": "",
                "gmarket": "",
            },
        }

        self.assertEqual(phase1_category(prod, categories), ("", "", "", ""))

    def test_incomplete_platform_codes_are_blocked_before_export(self):
        with self.assertRaisesRegex(ValueError, "A/G"):
            ensure_upload_category_codes("esm", "", "")

    def test_sold_object_beats_a_referenced_appliance(self):
        prod = Product(
            title="Microwave oven wall shelf",
            tag="steel, kitchen",
            subject_profile={
                "sold_object": "wall-mounted kitchen storage shelf",
                "sold_object_ko": "주방 선반",
                "sold_object_zh": "厨房置物架",
                "buyer_receives": "a metal storage shelf",
                "primary_function": "kitchen storage and support",
                "referenced_objects": ["microwave", "oven"],
                "confidence": 0.98,
            },
        )
        categories = {
            "Appliances>Kitchen Appliances>Microwave": self._codes("microwave"),
            "Home>Kitchen>Storage>Kitchen Shelf": self._codes("shelf"),
        }

        result = phase1_category(prod, categories)

        self.assertEqual(result[0], "Home>Kitchen>Storage>Kitchen Shelf")
        self.assertEqual(result[1:], ("shelf", "shelf-a", "shelf-g"))
        self.assertEqual(prod.result["_category_deepseek_calls"], 0)

    def test_sold_object_beats_its_contents_across_product_types(self):
        cases = [
            (
                {
                    "sold_object": "portable spice grinder",
                    "buyer_receives": "a handheld grinder",
                    "primary_function": "grinding seasonings",
                    "category_terms_ko": ["절구", "맷돌"],
                    "category_terms_zh": ["捣臼", "石磨"],
                    "referenced_objects": ["pepper", "salt"],
                    "confidence": 0.96,
                },
                {
                    "Food>Seasonings>Pepper": self._codes("pepper"),
                    "Home>Kitchen Tools>Mortar and Mill": self._codes("grinder"),
                },
                "Home>Kitchen Tools>Mortar and Mill",
            ),
            (
                {
                    "sold_object": "hanging storage basket",
                    "buyer_receives": "an under-shelf basket",
                    "primary_function": "organizing stored items",
                    "referenced_objects": ["underwear", "wardrobe", "shelf"],
                    "confidence": 0.97,
                },
                {
                    "Books>Art>Design": self._codes("design"),
                    "Home>Storage>Basket": self._codes("basket"),
                },
                "Home>Storage>Basket",
            ),
        ]

        for profile, categories, expected in cases:
            with self.subTest(expected=expected):
                prod = Product(title="noisy source title", tag="origin material", subject_profile=profile)
                self.assertEqual(phase1_category(prod, categories)[0], expected)

    def test_ambiguous_subject_reuses_at_most_one_category_call(self):
        original = processor.deepseek_chat
        calls = []
        try:
            processor.deepseek_chat = lambda *args, **kwargs: calls.append((args, kwargs)) or '''{
              "candidate_id": "C01",
              "same_sold_object": true,
              "confidence": 0.9,
              "evidence": "same physical item"
            }'''
            prod = Product(
                title="holder",
                subject_profile={
                    "sold_object": "multipurpose holder",
                    "buyer_receives": "a holder",
                    "primary_function": "holding objects",
                    "referenced_objects": [],
                    "confidence": 0.8,
                },
            )
            categories = {
                "Home>Holder": self._codes("holder-1"),
                "Office>Holder": self._codes("holder-2"),
            }

            result = phase1_category(prod, categories)

            self.assertIn(result[0], categories)
            self.assertLessEqual(len(calls), 1)
            self.assertEqual(prod.result["_category_deepseek_calls"], len(calls))
            if calls:
                prompt = calls[0][0][0]
                self.assertIn("multipurpose holder", prompt)
                self.assertNotIn("origin material", prompt)
        finally:
            processor.deepseek_chat = original

    @staticmethod
    def _codes(value):
        return {
            "esm_code": value,
            "auction": value + "-a",
            "gmarket": value + "-g",
        }


if __name__ == "__main__":
    unittest.main()
