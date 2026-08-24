import unittest

from config_manager import DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE, normalize_batch_size


class BatchConcurrencyTests(unittest.TestCase):
    def test_default_queue_and_image_concurrency_is_50(self):
        self.assertEqual(DEFAULT_BATCH_SIZE, 50)
        self.assertEqual(MAX_BATCH_SIZE, 50)
        self.assertEqual(normalize_batch_size(None), 50)

    def test_queue_concurrency_is_clamped_to_supported_range(self):
        self.assertEqual(normalize_batch_size(1), 1)
        self.assertEqual(normalize_batch_size("40"), 40)
        self.assertEqual(normalize_batch_size(50), 50)
        self.assertEqual(normalize_batch_size(500), 50)
        self.assertEqual(normalize_batch_size(0), 1)


if __name__ == "__main__":
    unittest.main()
