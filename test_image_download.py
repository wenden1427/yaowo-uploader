"""Image transport regression tests; no real network, model or storage calls."""

import io
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from unittest import mock

from PIL import Image

import image_download as transport


def image_bytes(fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format=fmt)
    return buf.getvalue()


class Response(io.BytesIO):
    def __init__(self, data=None, status=200, headers=None):
        super().__init__(image_bytes() if data is None else data)
        self.status = status
        self.headers = headers or {}


def http_error(code=429, retry_after=None):
    headers = {"Content-Type": "image/jpeg"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://source.test/img", code, "rejected", headers,
                                  io.BytesIO(image_bytes("JPEG")))


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ImageDownloadTests(unittest.TestCase):
    URL = "https://source.test/a.jpg"

    def setUp(self):
        self.clock = Clock()
        self.opener = mock.Mock()
        self.opener.open.side_effect = lambda *a, **kw: Response()
        self.factory = mock.Mock(return_value=self.opener)
        self.logs = []
        self.loader = self.make_loader()

    def make_loader(self, **kwargs):
        return transport.ImageDownloader(
            self.factory, self.logs.append, clock=self.clock, sleep=self.clock.sleep,
            wall_clock=lambda: 1000000000, jitter=lambda: 0, **kwargs)

    def test_browser_compatible_headers_and_original_url(self):
        self.loader.download(self.URL)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.URL)
        self.assertIn("AppleWebKit", request.get_header("User-agent"))
        self.assertIn("image/jpeg", request.get_header("Accept"))
        self.assertNotIn("svg", request.get_header("Accept"))
        self.assertIsNone(request.get_header("Cookie"))
        self.factory.assert_called_once_with()

    def test_accept_only_advertises_available_decoders(self):
        with mock.patch.object(transport.features, "get_supported", return_value=[]):
            header = transport.image_accept_header()
        self.assertNotIn("avif", header)
        self.assertNotIn("webp", header)
        with mock.patch.object(transport.features, "get_supported", return_value=["avif", "webp"]), \
             mock.patch.dict(Image.OPEN, {"AVIF": object(), "WEBP": object()}):
            header = transport.image_accept_header()
        self.assertIn("avif", header)
        self.assertIn("webp", header)

    def test_success_is_shared_across_repeated_consumers(self):
        first = self.loader.download(self.URL)
        self.assertEqual(first, self.loader.download(self.URL))
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertTrue(any("cache=hit" in item for item in self.logs))

    def test_cache_expiry(self):
        self.loader.download(self.URL)
        self.clock.now += 301
        self.loader.download(self.URL)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_cache_does_not_mix_signed_or_resized_urls(self):
        for suffix in ("?token=one", "?token=two", "?w=100", "?w=800"):
            self.loader.download(self.URL + suffix)
        self.assertEqual(self.opener.open.call_count, 4)
        self.assertFalse(any("token=" in line or "?w=" in line for line in self.logs))

    def test_logs_exclude_userinfo_paths_and_tokens(self):
        self.loader.download("https://account:password@source.test/private-product?token=secret-value")
        text = " ".join(self.logs)
        for secret in ("account", "password", "private-product", "secret-value"):
            self.assertNotIn(secret, text)
        self.assertIn("host=source.test", text)

    def test_cache_lru_and_byte_limit(self):
        self.loader = self.make_loader(cache_bytes=len(image_bytes()) * 2, cache_items=2)
        self.loader.download(self.URL)
        self.loader.download(self.URL + "2")
        self.loader.download(self.URL)
        self.loader.download(self.URL + "3")
        self.loader.download(self.URL)
        self.assertEqual(self.opener.open.call_count, 3)
        self.loader.download(self.URL + "2")
        self.assertEqual(self.opener.open.call_count, 4)
        self.assertLessEqual(self.loader._cached_size, len(image_bytes()) * 2)

    def test_image_larger_than_cache_is_returned_but_not_cached(self):
        self.loader = self.make_loader(cache_bytes=1)
        self.loader.download(self.URL)
        self.loader.download(self.URL)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual(self.loader._cached_size, 0)

    def test_invalidate_only_requested_image(self):
        self.loader.download(self.URL)
        self.loader.download(self.URL + "2")
        self.loader.invalidate(self.URL)
        self.loader.download(self.URL + "2")
        self.loader.download(self.URL)
        self.assertEqual(self.opener.open.call_count, 3)

    def test_network_error_retries_configured_route_without_bypassing_proxy(self):
        self.opener.open.side_effect = [OSError("network failed"), Response()]
        self.loader.download(self.URL)
        self.assertEqual(self.factory.call_args_list, [mock.call(), mock.call()])
        self.assertEqual(self.clock.sleeps, [1])

    def test_other_http_errors_do_not_add_retry(self):
        for code in (403, 404, 500):
            with self.subTest(code=code):
                self.factory.reset_mock()
                self.opener.open.side_effect = lambda *a, **kw: (_ for _ in ()).throw(http_error(code))
                with self.assertRaises(urllib.error.HTTPError):
                    self.loader.download(self.URL)
                self.assertEqual(self.factory.call_count, 1 if code in (403, 404) else 2)
                self.assertFalse(self.loader._cache)

    def test_invalid_payload_is_not_cached_as_image(self):
        self.opener.open.side_effect = lambda *a, **kw: Response(b"<html>try later</html>")
        with self.assertRaises(transport.ImageDownloadError) as raised:
            self.loader.download(self.URL)
        self.assertTrue(raised.exception.invalid_image)
        self.assertFalse(self.loader._cache)
        self.opener.open.side_effect = lambda *a, **kw: Response()
        self.assertEqual(self.loader.download(self.URL), image_bytes())

    def test_read_limit_protects_memory(self):
        with mock.patch.object(transport, "MAX_IMAGE_BYTES", 10), \
             self.assertRaisesRegex(transport.ImageDownloadError, "32MB"):
            self.loader.download(self.URL)
        self.assertFalse(self.loader._cache)

    def test_failed_429_jpeg_response_is_never_a_product_image(self):
        self.opener.open.side_effect = lambda *a, **kw: Response(image_bytes("JPEG"), 429)
        with self.assertRaises(transport.ImageDownloadError) as raised:
            self.loader.download(self.URL)
        self.assertEqual(raised.exception.original.code, 429)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(self.clock.sleeps, [4, 8])
        self.assertFalse(self.loader._cache)
        self.assertFalse(self.loader._flights)

    def test_retry_after_seconds_delays_retry(self):
        self.opener.open.side_effect = [http_error(retry_after="12"), Response()]
        self.loader.download(self.URL)
        self.assertEqual(self.clock.sleeps, [12])
        self.assertEqual(self.factory.call_args_list, [mock.call(), mock.call()])

    def test_retry_after_http_date(self):
        date = formatdate(1000000020, usegmt=True)
        self.opener.open.side_effect = [http_error(retry_after=date), Response()]
        self.loader.download(self.URL)
        self.assertEqual(self.clock.sleeps, [20])

    def test_invalid_retry_after_uses_backoff(self):
        self.opener.open.side_effect = [http_error(retry_after="later"), Response()]
        self.loader.download(self.URL)
        self.assertEqual(self.clock.sleeps, [4])

    def test_429_allows_only_one_extra_attempt_after_network_failure(self):
        self.opener.open.side_effect = [OSError("network failed"), http_error(), Response()]
        self.loader.download(self.URL)
        self.assertEqual(self.factory.call_args_list, [mock.call(), mock.call(), mock.call()])
        self.assertEqual(self.clock.sleeps, [1, 4])

    def test_long_retry_after_does_not_sleep_for_minutes_or_send_again(self):
        self.opener.open.side_effect = [http_error(retry_after="3600"), Response()]
        with self.assertRaises(transport.ImageDownloadError) as raised:
            self.loader.download(self.URL)
        self.assertEqual(raised.exception.retry_after, 3600)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertFalse(self.clock.sleeps)
        self.loader.invalidate(self.URL)
        with self.assertRaises(transport.ImageDownloadError):
            self.loader.download(self.URL)
        with self.assertRaises(transport.ImageDownloadError):
            self.loader.download(self.URL + "2")
        self.assertEqual(self.opener.open.call_count, 1)
        self.loader.download("https://another.test/a.jpg")
        self.assertEqual(self.opener.open.call_count, 2)

    def test_host_recovery_is_paced_but_other_hosts_and_cache_are_not(self):
        self.opener.open.side_effect = [http_error(), Response(), Response(), Response()]
        self.loader.download(self.URL)
        self.loader.download(self.URL)
        self.loader.download("https://another.test/a.jpg")
        self.assertEqual(self.clock.sleeps, [4])
        self.loader.download(self.URL + "2")
        self.assertEqual(self.clock.sleeps, [4, 0.5])
        self.clock.now += 61
        self.opener.open.side_effect = lambda *a, **kw: Response()
        self.loader.download(self.URL + "3")
        self.assertEqual(self.clock.sleeps, [4, 0.5])

    def test_repeated_429_can_recover_and_failure_is_not_sticky(self):
        self.opener.open.side_effect = lambda *a, **kw: (_ for _ in ()).throw(http_error())
        with self.assertRaises(transport.ImageDownloadError):
            self.loader.download(self.URL)
        self.opener.open.side_effect = lambda *a, **kw: Response()
        self.loader.download(self.URL)
        self.assertEqual(self.clock.sleeps, [4, 8, 16])
        self.assertIn(self.URL, self.loader._cache)

    def test_deadline_checked_between_read_chunks(self):
        response = Response()
        read = response.read

        def slow_read(n):
            self.clock.now += 91
            return read(n)

        response.read = slow_read
        self.opener.open.side_effect = [response]
        with self.assertRaises(TimeoutError):
            self.loader.download(self.URL)
        self.assertFalse(self.loader._cache)

    def test_concurrent_same_url_joins_one_request(self):
        joined, release = threading.Event(), threading.Event()
        count_lock = threading.Lock()
        joined_count = 0

        def log(message):
            nonlocal joined_count
            if "request=shared" in message:
                with count_lock:
                    joined_count += 1
                    if joined_count == 15:
                        joined.set()

        def open_image(*a, **kw):
            self.assertTrue(release.wait(5))
            return Response()

        loader = transport.ImageDownloader(self.factory, log)
        self.opener.open.side_effect = open_image
        with ThreadPoolExecutor(max_workers=16) as pool:
            jobs = [pool.submit(loader.download, self.URL) for _ in range(16)]
            try:
                self.assertTrue(joined.wait(3))
            finally:
                release.set()
            self.assertTrue(all(job.result(5) == image_bytes() for job in jobs))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_fifty_distinct_images_can_still_download_concurrently(self):
        barrier = threading.Barrier(50, timeout=10)

        def open_image(*a, **kw):
            barrier.wait()
            return Response()

        loader = transport.ImageDownloader(self.factory)
        self.opener.open.side_effect = open_image
        with ThreadPoolExecutor(max_workers=50) as pool:
            jobs = [pool.submit(loader.download, self.URL + str(i)) for i in range(50)]
            self.assertTrue(all(job.result(12) == image_bytes() for job in jobs))
        self.assertEqual(self.opener.open.call_count, 50)

    def test_refresh_during_download_does_not_repopulate_stale_cache(self):
        started, release = threading.Event(), threading.Event()

        def open_image(*a, **kw):
            started.set()
            self.assertTrue(release.wait(3))
            return Response()

        self.opener.open.side_effect = open_image
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.loader.download, self.URL)
            try:
                self.assertTrue(started.wait(2))
                self.loader.invalidate(self.URL)
            finally:
                release.set()
            self.assertEqual(job.result(3), image_bytes())
        self.assertFalse(self.loader._cache)
        self.loader.download(self.URL)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_pending_failure_wakes_waiters_and_can_be_retried(self):
        joined, release = threading.Event(), threading.Event()
        loader = transport.ImageDownloader(
            self.factory, lambda line: joined.set() if "request=shared" in line else None)

        def open_image(*a, **kw):
            self.assertTrue(release.wait(3))
            raise OSError("offline")

        self.opener.open.side_effect = open_image
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(loader.download, self.URL) for _ in range(2)]
            try:
                self.assertTrue(joined.wait(2))
            finally:
                release.set()
            for job in jobs:
                with self.assertRaises(OSError):
                    job.result(3)
        self.assertEqual(self.opener.open.call_count, 2)
        self.opener.open.side_effect = lambda *a, **kw: Response()
        self.assertEqual(loader.download(self.URL), image_bytes())

    def test_non_http_url_is_rejected(self):
        for url in ("", "file:///tmp/image.png", "https:///no-host"):
            with self.assertRaises(ValueError):
                self.loader.download(url)
        self.opener.open.assert_not_called()

    def test_api_client_uses_shared_downloader_and_refresh(self):
        import api_client
        with mock.patch.object(api_client, "_image_downloader", self.loader):
            self.assertEqual(api_client.download_image(self.URL), image_bytes())
            api_client.download_image(self.URL)
            self.assertEqual(self.opener.open.call_count, 1)
            api_client.invalidate_image_cache(self.URL)
            api_client.download_image(self.URL)
            self.assertEqual(self.opener.open.call_count, 2)

    def test_api_client_always_uses_central_proxy_resolution(self):
        import api_client
        url = self.URL + "-proxy-resolution"
        api_client.invalidate_image_cache(url)
        with mock.patch.object(api_client, "_make_opener", return_value=self.opener) as make, \
             mock.patch("network_utils._write_log"):
            api_client.download_image(url)
        make.assert_called_once_with(use_proxy=True)
        api_client.invalidate_image_cache(url)

    def test_configured_route_supports_no_proxy_or_tun(self):
        import network_utils
        with mock.patch.object(network_utils, "resolve_proxy", return_value=network_utils.ProxyDecision(None, "direct")), \
             mock.patch.object(network_utils.request, "build_opener", return_value=self.opener) as build, \
             mock.patch.object(network_utils, "_write_log"):
            loader = transport.ImageDownloader(lambda: network_utils.build_network_opener("proxy"))
            loader.download(self.URL)
        self.assertEqual(build.call_args.args[0].proxies, {})


if __name__ == "__main__":
    unittest.main()
