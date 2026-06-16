import base64
import io
import json
import unittest
import urllib.error

import api_client


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        payload = {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode("ascii")}]}
        return json.dumps(payload).encode("utf-8")


class FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        return FakeResponse()


class FailingOpener:
    def __init__(self, body=b"blocked by gateway"):
        self.requests = []
        self.body = body

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(self.body),
        )


class RouteApiTests(unittest.TestCase):
    def setUp(self):
        self._orig_get_config = api_client._get_config
        self._orig_make_opener = api_client._make_opener
        self._orig_download_image = api_client.download_image

    def tearDown(self):
        api_client._get_config = self._orig_get_config
        api_client._make_opener = self._orig_make_opener
        api_client.download_image = self._orig_download_image

    def test_generate_image_defaults_to_routeapi_model_1k(self):
        calls = []
        original_routeapi_generate = getattr(api_client, "routeapi_generate", None)

        def fake_routeapi_generate(prompt, img_urls=None, model="gpt-image-2-1k", size="1024x1024"):
            calls.append((prompt, img_urls, model, size))
            return b"ok"

        api_client._get_config = lambda: {"image_api": "routeapi"}
        api_client.routeapi_generate = fake_routeapi_generate
        try:
            result = api_client.generate_image("make it clean", ["http://example.com/a.jpg"])
        finally:
            if original_routeapi_generate is None:
                delattr(api_client, "routeapi_generate")
            else:
                api_client.routeapi_generate = original_routeapi_generate

        self.assertEqual(result, b"ok")
        self.assertEqual(calls, [("make it clean", ["http://example.com/a.jpg"], "gpt-image-2-1k", "1024x1024")])

    def test_routeapi_generate_sends_multipart_edit_request(self):
        opener = FakeOpener()
        proxy_flags = []
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }

        def fake_make_opener(use_proxy):
            proxy_flags.append(use_proxy)
            return opener

        api_client._make_opener = fake_make_opener
        api_client.download_image = lambda url: b"reference-image"

        result = api_client.routeapi_generate("make product photo", ["http://example.com/ref.jpg"])

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(proxy_flags, [True])
        self.assertEqual(len(opener.requests), 1)
        req, timeout = opener.requests[0]
        self.assertEqual(req.full_url, "https://api.1route.dev/v1/images/edits")
        self.assertEqual(timeout, 180)
        self.assertEqual(req.headers["Authorization"], "Bearer secret-key")
        self.assertIn("Mozilla/5.0", req.headers["User-agent"])
        self.assertIn("multipart/form-data", req.headers["Content-type"])
        self.assertIn(b'name="model"\r\n\r\ngpt-image-2-1k', req.data)
        self.assertIn(b'name="image"; filename="ref0.jpg"', req.data)

    def test_routeapi_generate_skips_unreachable_reference_images(self):
        opener = FakeOpener()
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener

        def fake_download(url):
            if url.endswith("bad.jpg"):
                raise Exception("HTTP Error 403: Forbidden")
            return b"reference-image"

        api_client.download_image = fake_download

        result = api_client.routeapi_generate(
            "make product photo",
            ["http://example.com/good.jpg", "http://example.com/bad.jpg"],
        )

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(len(opener.requests), 1)
        req, _ = opener.requests[0]
        self.assertIn(b'filename="ref0.jpg"', req.data)
        self.assertNotIn(b'filename="ref1.jpg"', req.data)

    def test_routeapi_generate_reports_http_403_response_body(self):
        opener = FailingOpener(b'{"error":"model not allowed"}')
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener
        api_client.download_image = lambda url: b"reference-image"

        with self.assertRaisesRegex(Exception, "routeapi HTTP 403 via proxy: .*model not allowed"):
            api_client.routeapi_generate("make product photo", ["http://example.com/ref.jpg"])

    def test_legacy_provider_removed(self):
        legacy_prefix = "hao" + "ming" + "ai"
        self.assertFalse(hasattr(api_client, f"{legacy_prefix}_generate"))
        self.assertFalse(hasattr(api_client, f"{legacy_prefix}_identify"))


if __name__ == "__main__":
    unittest.main()
