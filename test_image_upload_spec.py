import io
import unittest

from PIL import Image

from api_client import CloudinaryProvider
from models import Product
import processor


def make_jpeg(width, height, color=(240, 240, 240)):
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def make_large_noisy_jpeg(width=6000, height=6000):
    img = Image.effect_noise((width, height), 100).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def image_size(image_bytes):
    return Image.open(io.BytesIO(image_bytes)).size


class ImageUploadSpecTests(unittest.TestCase):
    def setUp(self):
        self._orig_download_image = processor.download_image
        self._orig_generate_image = processor.generate_image

    def tearDown(self):
        processor.download_image = self._orig_download_image
        processor.generate_image = self._orig_generate_image

    def test_cloudinary_upload_spec_upscales_small_images_to_600_square_minimum(self):
        fixed = CloudinaryProvider._ensure_upload_spec(make_jpeg(494, 493))

        width, height = image_size(fixed)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)

    def test_cloudinary_upload_spec_hard_limits_large_images_to_2mb(self):
        original = make_large_noisy_jpeg()
        self.assertGreater(len(original), 2_000_000)

        fixed = CloudinaryProvider._ensure_upload_spec(original)
        width, height = image_size(fixed)

        self.assertLessEqual(len(fixed), 2_000_000)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)

    def test_cloudinary_upload_spec_targets_300kb(self):
        original = make_large_noisy_jpeg(1600, 1600)
        self.assertGreater(len(original), 300_000)

        fixed = CloudinaryProvider._ensure_upload_spec(original)
        width, height = image_size(fixed)

        self.assertLessEqual(len(fixed), 300_000)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)

    def test_cloudinary_upload_spec_pads_thin_images_without_huge_upscale(self):
        fixed = CloudinaryProvider._ensure_upload_spec(make_jpeg(1500, 121))
        width, height = image_size(fixed)

        self.assertLessEqual(len(fixed), 300_000)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)
        self.assertLessEqual(max(width, height), 1200)

    def test_processor_image_spec_hard_limits_large_images_to_2mb(self):
        original = make_large_noisy_jpeg()
        self.assertGreater(len(original), 2_000_000)

        fixed = processor._ensure_image_meets_spec(original)
        width, height = image_size(fixed)

        self.assertLessEqual(len(fixed), 2_000_000)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)

    def test_processor_image_spec_targets_300kb(self):
        original = make_large_noisy_jpeg(1600, 1600)
        self.assertGreater(len(original), 300_000)

        fixed = processor._ensure_image_meets_spec(original)
        width, height = image_size(fixed)

        self.assertLessEqual(len(fixed), 300_000)
        self.assertGreaterEqual(width, 600)
        self.assertGreaterEqual(height, 600)

    def test_variant_collection_skips_images_that_cannot_be_uploaded(self):
        prod = Product(parent_sku="P1", main_img="http://img/main.jpg", platform="shein")
        prod.variant_imgs = ["http://img/small.jpg"]
        processor.download_image = lambda url: make_jpeg(494, 493)

        class FailingStorage:
            def upload(self, image_bytes, filename="image.jpg"):
                raise Exception("upload failed")

        self.assertEqual(processor._collect_variant_imgs(prod, FailingStorage()), "")

    def test_main_image_generation_uploads_spec_safe_image(self):
        prod = Product(parent_sku="P1", main_img="http://img/main.jpg", platform="shein")
        processor.generate_image = lambda prompt, refs: make_jpeg(494, 493)
        uploaded_sizes = []

        class InspectingStorage:
            def upload(self, image_bytes, filename="image.jpg"):
                uploaded_sizes.append(image_size(image_bytes))
                return "http://cloudinary/main.jpg"

        result = processor._gen_main_image(prod, "prompt", InspectingStorage())

        self.assertEqual(result, "http://cloudinary/main.jpg")
        self.assertEqual(uploaded_sizes, [(601, 600)])

    def test_ai_source_image_is_excluded_from_variant_uploads(self):
        prod = Product(
            parent_sku="A1",
            main_img="https://ae-pic-a1.aliexpress-media.com/kf/Smain.jpg",
            platform="aliexpress",
        )
        prod.variant_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg_960x960.jpg",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sred.jpg",
        ]
        prod.extra_imgs = [
            "https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg_120x120.jpg_.webp",
            "https://ae-pic-a1.aliexpress-media.com/kf/Sdetail.jpg",
        ]
        refs_seen = []
        downloaded = []
        processor.generate_image = lambda prompt, refs: refs_seen.extend(refs) or make_jpeg(640, 640)
        processor.download_image = lambda url: downloaded.append(url) or make_jpeg(640, 640)

        class RecordingStorage:
            def __init__(self):
                self.names = []

            def upload(self, image_bytes, filename="image.jpg"):
                self.names.append(filename)
                return f"http://cloud/{filename}"

        storage = RecordingStorage()

        main_url = processor._gen_main_image(prod, "prompt", storage)
        variant_urls = processor._collect_variant_imgs(prod, storage)

        self.assertEqual(main_url, "http://cloud/main_A1.jpg")
        self.assertEqual(prod.ai_source_image_url, "https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg")
        self.assertEqual(refs_seen[0], prod.ai_source_image_url)
        self.assertNotIn("https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg_960x960.jpg", downloaded)
        self.assertNotIn("https://ae-pic-a1.aliexpress-media.com/kf/Sblue.jpg_120x120.jpg_.webp", downloaded)
        self.assertEqual(
            downloaded,
            [
                "https://ae-pic-a1.aliexpress-media.com/kf/Sred.jpg",
            ],
        )
        self.assertIn("http://cloud/var_", variant_urls)


if __name__ == "__main__":
    unittest.main()
