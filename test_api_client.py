import base64
import io
import json
import unittest
import urllib.error
from unittest import mock

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


class JsonResponse(FakeResponse):
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RouteJobOpener:
    def __init__(self, snapshots=None, result=None, status_errors=None):
        self.requests = []
        self.snapshots = list(snapshots or [{"phase": "completed"}])
        self.status_errors = list(status_errors or [])
        self.result = result or {
            "images": [{
                "base64": base64.b64encode(b"image-bytes").decode("ascii"),
                "url": "",
                "mimeType": "image/png",
            }],
            "usage": {},
        }

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if req.full_url.endswith("/edits/jobs"):
            return JsonResponse({
                "jobId": "job_test",
                "phase": "queued",
                "durationGuidance": {"timeoutSeconds": 450},
            }, status=202)
        if req.full_url.endswith("/result"):
            return JsonResponse(self.result)
        if self.status_errors:
            raise self.status_errors.pop(0)
        return JsonResponse(self.snapshots.pop(0))


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


class TextResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode("utf-8")


class TextOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        return TextResponse()


class TextAIClientTests(unittest.TestCase):
    def test_bailian_is_preferred_and_disables_thinking(self):
        opener = TextOpener()
        with mock.patch.object(
            api_client, "get_credential", return_value="encrypted-user-key"
        ), mock.patch.object(
            api_client, "_get_config", return_value={"text_model": "qwen3.7-flash"}
        ), mock.patch.object(
            api_client, "_make_opener", return_value=opener
        ):
            self.assertEqual(api_client.text_chat("hello", max_tokens=80, temp=0), "ok")

        request, timeout = opener.requests[0]
        payload = json.loads(request.data)
        self.assertIn("dashscope.aliyuncs.com", request.full_url)
        self.assertEqual(payload["model"], "qwen3.7-flash")
        self.assertEqual(payload["max_completion_tokens"], 80)
        self.assertFalse(payload["enable_thinking"])
        self.assertNotIn("encrypted-user-key", request.data.decode("utf-8"))
        self.assertEqual(timeout, 120)

    def test_existing_deepseek_config_remains_compatible_without_bailian(self):
        opener = TextOpener()
        with mock.patch.object(
            api_client, "get_credential", return_value=""
        ), mock.patch.object(
            api_client,
            "_get_config",
            return_value={
                "deepseek_key": "old-machine-key",
                "deepseek_url": "https://api.deepseek.com/v1/chat/completions",
            },
        ), mock.patch.object(
            api_client, "_make_opener", return_value=opener
        ):
            self.assertEqual(api_client.text_chat("hello", max_tokens=60, temp=0), "ok")

        request, _ = opener.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["max_tokens"], 60)
        self.assertNotIn("max_completion_tokens", payload)

    def test_bailian_task_can_override_the_default_model(self):
        opener = TextOpener()
        with mock.patch.object(
            api_client, "get_credential", return_value="encrypted-user-key"
        ), mock.patch.object(
            api_client, "_get_config", return_value={"text_model": "qwen3.7-flash"}
        ), mock.patch.object(
            api_client, "_make_opener", return_value=opener
        ):
            api_client.text_chat(
                "choose category", max_tokens=80, temp=0, model="qwen3.7-plus"
            )

        payload = json.loads(opener.requests[0][0].data)
        self.assertEqual(payload["model"], "qwen3.7-plus")


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

        def fake_routeapi_generate(prompt, img_urls=None,
                                   model="openai/gpt-image-2", size="1024x1024"):
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
        self.assertEqual(calls, [(
            "make it clean",
            ["http://example.com/a.jpg"],
            "openai/gpt-image-2",
            "1024x1024",
        )])

    def test_make_opener_uses_explicit_direct_and_proxy_routes(self):
        sent_routes = []
        sentinel = object()
        with mock.patch(
            "network_utils.build_network_opener",
            side_effect=lambda route: sent_routes.append(route) or sentinel,
        ):
            self.assertIs(api_client._make_opener(False), sentinel)
            self.assertIs(api_client._make_opener(True), sentinel)
        self.assertEqual(sent_routes, ["direct", "proxy"])

    def test_routeapi_generate_submits_and_collects_async_edit_job(self):
        opener = RouteJobOpener(snapshots=[
            {"phase": "running"},
            {"phase": "completed"},
        ])
        proxy_flags = []
        api_client._get_config = lambda: {
            "routeapi_url": "https://image-api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }

        def fake_make_opener(use_proxy):
            proxy_flags.append(use_proxy)
            return opener

        api_client._make_opener = fake_make_opener
        api_client.download_image = lambda url: make_jpeg()

        with mock.patch.object(api_client.time, "sleep", return_value=None):
            result = api_client.routeapi_generate(
                "make product photo", ["http://example.com/ref.jpg"]
            )

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(proxy_flags, [True])
        self.assertEqual(len(opener.requests), 4)
        req, timeout = opener.requests[0]
        self.assertEqual(
            req.full_url,
            "https://api.1route.dev/api/v1/images/edits/jobs",
        )
        self.assertEqual(req.method, "POST")
        self.assertEqual(timeout, 120)
        self.assertEqual(req.headers["Authorization"], "Bearer secret-key")
        self.assertIn("Mozilla/5.0", req.headers["User-agent"])
        self.assertEqual(req.headers["Content-type"], "application/json")
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], "openai/gpt-image-2")
        self.assertEqual(payload["size"], "1024x1024")
        self.assertEqual(len(payload["images"]), 1)
        self.assertTrue(payload["images"][0]["dataUrl"].startswith(
            "data:image/jpeg;base64,"
        ))
        self.assertTrue(opener.requests[-1][0].full_url.endswith(
            "/api/v1/images/jobs/job_test/result"
        ))

    def test_routeapi_generate_skips_unreachable_reference_images(self):
        opener = RouteJobOpener()
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener

        def fake_download(url):
            if url.endswith("bad.jpg"):
                raise Exception("HTTP Error 403: Forbidden")
            return make_jpeg()

        api_client.download_image = fake_download

        result = api_client.routeapi_generate(
            "make product photo",
            ["http://example.com/good.jpg", "http://example.com/bad.jpg"],
        )

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(len(opener.requests), 3)
        req, _ = opener.requests[0]
        payload = json.loads(req.data)
        self.assertEqual(len(payload["images"]), 1)

    def test_routeapi_generate_reports_http_403_response_body(self):
        opener = FailingOpener(b'{"error":"model not allowed"}')
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev/v1/images/edits",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener
        api_client.download_image = lambda url: make_jpeg()

        with self.assertRaisesRegex(Exception, "routeapi HTTP 403 via proxy: .*model not allowed"):
            api_client.routeapi_generate("make product photo", ["http://example.com/ref.jpg"])

    def test_routeapi_generate_reports_terminal_job_error(self):
        opener = RouteJobOpener(snapshots=[{
            "phase": "failed",
            "error": {
                "code": "upstream_error",
                "type": "upstream_error",
                "message": "Image generation failed",
            },
        }])
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener
        api_client.download_image = lambda url: make_jpeg()

        with self.assertRaisesRegex(
            RuntimeError, "routeapi job failed: upstream_error.*Image generation failed"
        ):
            api_client.routeapi_generate(
                "make product photo", ["http://example.com/ref.jpg"]
            )

    def test_routeapi_status_poll_retries_without_resubmitting_job(self):
        opener = RouteJobOpener(
            snapshots=[{"phase": "completed"}],
            status_errors=[urllib.error.URLError("temporary disconnect")],
        )
        api_client._get_config = lambda: {
            "routeapi_url": "https://api.1route.dev",
            "routeapi_key": "secret-key",
        }
        api_client._make_opener = lambda use_proxy: opener
        api_client.download_image = lambda url: make_jpeg()

        with mock.patch.object(api_client.time, "sleep", return_value=None):
            result = api_client.routeapi_generate(
                "make product photo", ["http://example.com/ref.jpg"]
            )

        self.assertEqual(result, b"image-bytes")
        submit_requests = [
            req for req, _ in opener.requests if req.full_url.endswith("/edits/jobs")
        ]
        self.assertEqual(len(submit_requests), 1)

    def test_routeapi_legacy_url_and_model_are_normalized(self):
        self.assertEqual(
            api_client._routeapi_base_url(
                "https://image-api.1route.dev/v1/images/edits"
            ),
            "https://api.1route.dev/api/v1/images",
        )
        self.assertEqual(
            api_client._routeapi_model_name("gpt-image-2-1k"),
            "openai/gpt-image-2",
        )

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
