import unittest

from models import Product
from processor import build_generation_prompt, collect_generation_refs


class ImageReferenceTests(unittest.TestCase):
    def test_shein_refs_use_main_image_as_base_plus_extra_images(self):
        prod = Product(parent_sku="S1", main_img="http://img/main.jpg", platform="shein")
        prod.extra_imgs = [
            "http://img/main.jpg",
            "http://img/extra1.jpg",
            "http://img/extra2.jpg",
            "http://img/extra3.jpg",
            "http://img/extra4.jpg",
            "http://img/extra5.jpg",
            "http://img/extra6.jpg",
        ]
        prod.variant_imgs = ["http://img/variant-red.jpg"]

        self.assertEqual(
            collect_generation_refs(prod),
            [
                "http://img/main.jpg",
                "http://img/extra1.jpg",
                "http://img/extra2.jpg",
                "http://img/extra3.jpg",
                "http://img/extra4.jpg",
                "http://img/extra5.jpg",
            ],
        )

    def test_aliexpress_refs_use_first_variant_as_base_plus_public_extra_images(self):
        prod = Product(parent_sku="A1", main_img="http://img/shein-main.jpg", platform="aliexpress")
        prod.extra_imgs = [
            "http://img/shein-main.jpg",
            "http://img/public1.jpg",
            "http://img/public2.jpg",
            "http://img/public3.jpg",
            "http://img/public4.jpg",
            "http://img/public5.jpg",
        ]
        prod.variant_imgs = [
            "http://img/variant-blue.jpg",
            "http://img/variant-red.jpg",
        ]

        self.assertEqual(
            collect_generation_refs(prod),
            [
                "http://img/variant-blue.jpg",
                "http://img/shein-main.jpg",
                "http://img/public1.jpg",
                "http://img/public2.jpg",
                "http://img/public3.jpg",
                "http://img/public4.jpg",
            ],
        )

    def test_generation_prompt_marks_first_reference_as_base(self):
        prompt = build_generation_prompt("Make a clean product photo")

        self.assertIn("第一张参考图", prompt)
        self.assertIn("基底图", prompt)
        self.assertIn("后续参考图", prompt)
        self.assertTrue(prompt.endswith("Make a clean product photo"))


if __name__ == "__main__":
    unittest.main()
