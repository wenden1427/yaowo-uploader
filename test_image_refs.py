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

    def test_generation_prompt_uses_first_reference_as_product_baseline_only(self):
        prompt = build_generation_prompt("Make a clean product photo")

        self.assertIn("第一张参考图作为产品主体基准", prompt)
        self.assertIn("保持第一张图中的产品颜色、型号、款式和关键识别特征一致", prompt)
        self.assertIn("不要照搬第一张图的原始构图、背景、光线和拍摄角度", prompt)
        self.assertIn("可以根据用户提示重新设计背景、光线、构图、角度、质感和商业场景", prompt)
        self.assertIn("后续参考图", prompt)
        self.assertNotIn("基底图", prompt)
        self.assertNotIn("必须以", prompt)
        self.assertNotIn("为准", prompt)
        self.assertTrue(prompt.endswith("Make a clean product photo"))


if __name__ == "__main__":
    unittest.main()
