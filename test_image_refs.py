import unittest

from models import Product
from processor import (
    build_generation_prompt,
    collect_generation_refs,
    collect_upload_image_candidates,
    normalize_image_url_for_dedupe,
)


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

    def test_aliexpress_refs_use_first_variant_as_base_plus_other_variants_only(self):
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
                "http://img/variant-red.jpg",
            ],
        )

    def test_aliexpress_refs_fallback_to_main_when_no_variant_image(self):
        prod = Product(
            parent_sku="A2",
            main_img="https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg",
            platform="aliexpress",
        )
        prod.extra_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_120x120.jpg_.webp",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail.jpg",
        ]

        self.assertEqual(
            collect_generation_refs(prod),
            [
                "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg",
                "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail.jpg",
            ],
        )

    def test_aliexpress_image_candidates_dedupe_thumbnail_variants(self):
        prod = Product(
            parent_sku="A3",
            main_img="https://ae-pic-a1.aliexpress-media.com/kf/Smain.jpg",
            platform="aliexpress",
        )
        prod.variant_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_120x120.jpg_.webp",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_960x960.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sred.jpg",
        ]
        prod.extra_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail.jpg",
        ]

        candidates = collect_upload_image_candidates(prod)

        self.assertEqual(
            candidates.main,
            "https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_120x120.jpg_.webp",
        )
        self.assertEqual(
            candidates.secondary,
            [
                "https://ae-pic-a1.aliexpress-media.com/kf/Sred.jpg",
            ],
        )

    def test_aliexpress_image_candidates_use_extra_images_only_without_variants(self):
        prod = Product(
            parent_sku="A4",
            main_img="https://ae-pic-a1.aliexpress-media.com/kf/Smain.jpg",
            platform="aliexpress",
        )
        prod.extra_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Smain.jpg_960x960.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail1.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail2.jpg",
        ]

        candidates = collect_upload_image_candidates(prod)

        self.assertEqual(candidates.main, "https://ae-pic-a1.aliexpress-media.com/kf/Smain.jpg")
        self.assertEqual(
            candidates.secondary,
            [
                "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail1.jpg",
                "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail2.jpg",
            ],
        )

    def test_aliexpress_image_url_normalization_ignores_size_suffixes(self):
        self.assertEqual(
            normalize_image_url_for_dedupe("https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg"),
            normalize_image_url_for_dedupe("https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_120x120.jpg_.webp"),
        )
        self.assertEqual(
            normalize_image_url_for_dedupe("https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg"),
            normalize_image_url_for_dedupe("https://ae-pic-a1.aliexpress-media.com/kf/Sabc.jpg_960x960.jpg"),
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
