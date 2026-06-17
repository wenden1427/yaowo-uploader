import base64
import io
import json
import unittest
import urllib.error

import api_client


def make_jpeg(width=640, height=640):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class FakeResponse:
    status = 200

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


class EmptyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b""


class RecordingOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        return EmptyResponse()


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


class StorageProviderTests(unittest.TestCase):
    def setUp(self):
        self._orig_make_opener = api_client._make_opener

    def tearDown(self):
        api_client._make_opener = self._orig_make_opener

    def test_create_storage_provider_supports_tencent_cos(self):
        provider = api_client.create_storage_provider({
            "storage": {
                "provider": "tencent_cos",
                "secret_id": "sid",
                "secret_key": "skey",
                "bucket": "yaowoo-1443995558",
                "region": "ap-hongkong",
                "prefix": "gmarket/test",
            }
        })

        self.assertIsInstance(provider, api_client.TencentCOSProvider)

    def test_create_storage_provider_uses_tencent_cos_defaults(self):
        provider = api_client.create_storage_provider({
            "storage": {
                "provider": "tencent_cos",
                "secret_id": "sid",
                "secret_key": "skey",
            }
        })

        self.assertEqual(provider.bucket, "yaowoo-1443995558")
        self.assertEqual(provider.region, "ap-hongkong")
        self.assertEqual(provider.prefix, "gmarket/uploads")
        self.assertEqual(
            provider.base_url,
            "https://yaowoo-1443995558.cos.ap-hongkong.myqcloud.com",
        )

    def test_tencent_cos_upload_puts_jpeg_and_returns_public_url(self):
        opener = RecordingOpener()
        proxy_flags = []
        api_client._make_opener = lambda use_proxy: (proxy_flags.append(use_proxy) or opener)
        provider = api_client.TencentCOSProvider(
            secret_id="sid",
            secret_key="skey",
            bucket="yaowoo-1443995558",
            region="ap-hongkong",
            prefix="gmarket/test",
        )

        result = provider.upload(make_jpeg(), "main P1.png")

        self.assertTrue(result.startswith(
            "https://yaowoo-1443995558.cos.ap-hongkong.myqcloud.com/gmarket/test/"
        ))
        self.assertTrue(result.endswith(".jpg"))
        self.assertEqual(proxy_flags, [True])
        self.assertEqual(len(opener.requests), 1)
        req, timeout = opener.requests[0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertEqual(timeout, 60)
        self.assertLessEqual(len(req.data), 300_000)
        self.assertEqual(req.headers["Content-type"], "image/jpeg")
        self.assertIn("q-sign-algorithm=sha1", req.headers["Authorization"])
        self.assertIn("Host", req.headers)
        self.assertIn("yaowoo-1443995558.cos.ap-hongkong.myqcloud.com", req.full_url)


if __name__ == "__main__":
    unittest.main()
