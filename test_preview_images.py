"""Preview failures must not hide independently available generated images."""
import io
import socket
import ssl
import threading
import time
import unittest
import urllib.error
from collections import OrderedDict
from unittest import mock

from PIL import Image

import api_client
import gui
from models import Product, ProductStatus


class Canvas:
    def __init__(self):
        self.text = []

    def delete(self, *args):
        self.text.clear()

    def create_text(self, *args, **kwargs):
        self.text.append(kwargs["text"])

    def create_image(self, *args, **kwargs):
        pass

    def update_idletasks(self):
        pass

    def winfo_width(self):
        return 180

    def winfo_height(self):
        return 180


class QueuedRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def drain(self):
        while self.callbacks:
            self.callbacks.pop(0)()


def app_for_preview():
    app = gui.UploaderApp.__new__(gui.UploaderApp)
    app.root = QueuedRoot()
    app._preview_token = 1
    app._img_orig, app._img_gen, app._img_ref = Canvas(), Canvas(), Canvas()
    app._log_text = mock.Mock()
    app._canvas_show_image = mock.Mock()
    return app


def wait_load(app, prod):
    threads = app._load_preview_images(prod, 1)
    for thread in threads or []:
        thread.join(timeout=3)
        assert not thread.is_alive()
    app.root.drain()


class PreviewImagesTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = mock.patch("network_utils._write_log")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_original_failure_still_downloads_generated_image(self):
        app = app_for_preview()
        prod = Product(parent_sku="P1", main_img="https://source.test/original.jpg",
                       result={"Z": "https://cloud.test/generated.jpg"})

        def download(url):
            if url == prod.main_img:
                raise ssl.SSLError("TLS handshake failed")
            return b"generated"

        with mock.patch.object(api_client, "download_image", side_effect=download) as load:
            wait_load(app, prod)
        self.assertEqual(load.call_count, 2)
        self.assertIn("TLS", " ".join(app._img_orig.text))
        self.assertTrue(any(call.args[:2] == (app._img_gen, b"generated")
                            for call in app._canvas_show_image.call_args_list))
        self.assertTrue(prod.logs)

    def test_delayed_callbacks_bind_the_correct_bytes_and_reuse_reference(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a", result={"Z": "https://cloud.test/b"})
        with mock.patch.object(api_client, "download_image", side_effect=lambda url: url.encode()) as load:
            wait_load(app, prod)
        rendered = {call.args[0]: call.args[1] for call in app._canvas_show_image.call_args_list}
        self.assertEqual(rendered[app._img_orig], prod.main_img.encode())
        self.assertEqual(rendered[app._img_gen], prod.result["Z"].encode())
        self.assertEqual(rendered[app._img_ref], prod.main_img.encode())
        self.assertEqual(load.call_count, 2)

    def test_slow_original_does_not_block_generated_request(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a", result={"Z": "https://cloud.test/b"})
        release, started = threading.Event(), threading.Event()
        child_threads = []

        def download(url):
            if url == prod.main_img:
                release.wait(3)
            else:
                started.set()
            return b"image"

        def start():
            child_threads.extend(app._load_preview_images(prod, 1) or [])

        with mock.patch.object(api_client, "download_image", side_effect=download):
            caller = threading.Thread(target=start)
            caller.start()
            try:
                self.assertTrue(started.wait(1), "Generated request blocked by source request")
            finally:
                release.set()
                caller.join(3)
                for thread in child_threads:
                    thread.join(3)

    def test_stale_callbacks_cannot_replace_current_preview(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a")
        with mock.patch.object(api_client, "download_image", return_value=b"image"):
            threads = app._load_preview_images(prod, 1)
            for thread in threads or []:
                thread.join(3)
            app._preview_token = 2
            app.root.drain()
        app._canvas_show_image.assert_not_called()
        self.assertEqual(prod.logs, [])

    def test_decode_error_is_reported_in_canvas_not_left_loading(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a", result={"Z": "https://cloud.test/b"})
        app._canvas_show_image.side_effect = ValueError("cannot decode image")
        with mock.patch.object(api_client, "download_image", return_value=b"invalid"):
            wait_load(app, prod)
        self.assertIn("解码", " ".join(app._img_gen.text))
        self.assertIn("cannot decode image", " ".join(prod.logs))

    def test_same_url_for_all_canvases_downloaded_once_and_cached(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a", result={"Z": "https://source.test/a"})
        with mock.patch.object(api_client, "download_image", return_value=b"image") as load:
            wait_load(app, prod)
            wait_load(app, prod)
        load.assert_called_once_with(prod.main_img)
        self.assertEqual(app._canvas_show_image.call_count, 6)

    def test_failed_download_is_not_cached(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a")
        with mock.patch.object(api_client, "download_image", side_effect=[TimeoutError(), b"ok"]) as load:
            wait_load(app, prod)
            wait_load(app, prod)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(app._preview_bytes_cache[prod.main_img][1], b"ok")

    def test_refresh_evicts_only_current_urls_and_never_generates(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a", result={"Z": "https://cloud.test/b"})
        app._preview_product = prod
        app._preview_bytes_cache = OrderedDict(
            (url, (time.monotonic(), b"old")) for url in (prod.main_img, prod.result["Z"], "other"))
        app._show_preview = lambda p: wait_load(app, p)
        with mock.patch.object(api_client, "download_image", return_value=b"new") as load, \
             mock.patch.object(api_client, "invalidate_image_cache") as invalidate, \
             mock.patch.object(api_client, "generate_image") as generate, \
             mock.patch.object(api_client, "create_storage_provider") as storage:
            app._refresh_preview_images()
        self.assertEqual(load.call_count, 2)
        self.assertCountEqual(invalidate.call_args_list,
                              [mock.call(prod.main_img), mock.call(prod.result["Z"])])
        generate.assert_not_called()
        storage.assert_not_called()
        self.assertEqual(app._preview_bytes_cache[prod.main_img][1], b"new")
        self.assertEqual(app._preview_bytes_cache["other"][1], b"old")

    def test_429_cooldown_and_decode_error_have_specific_explanations(self):
        from image_download import ImageDownloadError
        original = urllib.error.HTTPError("https://source.test/a", 429, "rejected", {}, None)
        limited = ImageDownloadError("wait", original=original, retry_after=120)
        hint = gui.UploaderApp._preview_error_text(limited)
        self.assertIn("HTTP 429", hint)
        self.assertIn("120", hint)
        invalid = ImageDownloadError("bad image", invalid_image=True)
        self.assertIn("解码", gui.UploaderApp._preview_error_text(invalid))

    def test_selected_preview_auto_refreshes_when_main_url_changes(self):
        app = app_for_preview()
        prod = Product(main_img="https://source.test/a")
        app._tree = mock.Mock()
        app._tree.get_children.return_value = ()
        app._preview_product = prod
        app._preview_image_state = app._image_preview_state(prod)
        app._show_preview = mock.Mock()
        app.update_product_status(0, prod)
        app._show_preview.assert_not_called()
        prod.result["Z"] = "https://cloud.test/new"
        app.update_product_status(0, prod)
        app._show_preview.assert_called_once_with(prod)
        app._show_preview.reset_mock()
        app.update_product_status(1, Product())
        app._show_preview.assert_not_called()

    def test_failed_generation_is_distinct_from_preview_download_failure(self):
        app = app_for_preview()
        prod = Product(status=ProductStatus.FAILED, result={"_main_image_error": "model unavailable"})
        wait_load(app, prod)
        self.assertIn("生图或上传失败", " ".join(app._img_gen.text))
        self.assertNotIn("加载中", " ".join(app._img_gen.text))

    def test_error_explanation_and_log_redact_secrets(self):
        errors = [
            (ssl.SSLError("EOF"), "SSL"),
            (socket.gaierror(11001, "getaddrinfo failed"), "DNS"),
            (TimeoutError("timeout"), "超时"),
            (urllib.error.URLError("Tunnel connection failed: 502"), "代理"),
            (urllib.error.HTTPError("https://image.test/private?token=secret", 403,
                                    "Forbidden", None, None), "HTTP 403"),
        ]
        for error, expected in errors:
            with self.subTest(expected=expected):
                try:
                    self.assertIn(expected, gui.UploaderApp._preview_error_text(error))
                finally:
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
        app = app_for_preview()
        prod = Product(main_img="https://source.test/private?token=hidden-value")
        error = RuntimeError("Bearer private-bearer https://source.test/a?token=hidden-value")
        with mock.patch.object(api_client, "download_image", side_effect=error):
            wait_load(app, prod)
        logs = " ".join(prod.logs)
        self.assertNotIn("private-bearer", logs)
        self.assertNotIn("hidden-value", logs)

    def test_filtered_selection_uses_stable_product_id(self):
        app = app_for_preview()
        app.products = [Product(parent_sku=f"P{i}") for i in range(3)]
        app._tree = mock.Mock()
        app._tree.selection.return_value = ("2",)
        app._tree.index.return_value = 0
        app._show_preview = mock.Mock()
        app._on_product_select(None)
        app._show_preview.assert_called_once_with(app.products[2])
        app._tree.index.assert_not_called()

    def test_canvas_normalizes_palette_and_cmyk_images(self):
        app = gui.UploaderApp.__new__(gui.UploaderApp)
        canvas = Canvas()
        for mode, fmt in (("P", "PNG"), ("RGBA", "PNG"), ("CMYK", "JPEG")):
            with self.subTest(mode=mode):
                data = io.BytesIO()
                Image.new(mode, (60, 90)).save(data, format=fmt)
                with mock.patch.object(gui.tk, "PhotoImage") as photo:
                    app._canvas_show_image(canvas, data.getvalue(), "preview")
                self.assertEqual(Image.open(io.BytesIO(photo.call_args.kwargs["data"])).mode, "RGB")


if __name__ == "__main__":
    unittest.main()
