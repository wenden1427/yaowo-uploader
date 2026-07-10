import unittest

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


if __name__ == "__main__":
    unittest.main()
