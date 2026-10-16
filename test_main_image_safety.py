"""Failed generation must never produce a successful row with an empty main URL."""
import unittest
from contextlib import ExitStack
from unittest import mock

from openpyxl import Workbook

import gui
import processor
from models import Product, ProductStatus
from test_preview_images import QueuedRoot


def mock_phases(stack):
    for module in ("processor", "config_manager"):
        for name, result in (
            ("load_config", {"batch_size": 50}),
            ("load_prompts", {"generic": "test prompt"}),
            ("load_categories", []), ("load_banned_words", []),
        ):
            stack.enter_context(mock.patch(f"{module}.{name}", return_value=result))
    for name, result in (
        ("phase1_title", "test title"), ("phase1_category", ("test category", "K", "L", "M")),
        ("ensure_upload_category_codes", None), ("_get_category_zh", "test category"),
        ("phase1_price", 1000), ("phase1_w_type", "1"),
        ("phase1_x_attr", "color"), ("phase1_y_list", "test variant"),
        ("load_store_profiles", {}), ("category_fields", {}),
    ):
        stack.enter_context(mock.patch(f"processor.{name}", return_value=result))
    stack.enter_context(mock.patch("gui.load_store_profiles", return_value={}))
    stack.enter_context(mock.patch("api_client.create_storage_provider"))
    stack.enter_context(mock.patch("processor.create_storage_provider"))


def regen_app(products):
    app = gui.UploaderApp.__new__(gui.UploaderApp)
    app.products = products
    app.root = QueuedRoot()
    app._title_mode_var = mock.Mock(get=lambda: "brand_only")
    app._img_profile_var = mock.Mock(get=lambda: "generic")
    app._effective_store_id = lambda: ""
    app._regen_btn = mock.Mock()
    app._regen_status = mock.Mock()
    app.update_product_status = mock.Mock()
    app._show_preview = mock.Mock()
    return app


class MainImageSafetyTests(unittest.TestCase):
    def test_upload_failure_reuses_generated_bytes_without_another_model_call(self):
        prod = Product(parent_sku="P1", main_img="https://source.test/ref")
        storage = mock.Mock()
        storage.upload.side_effect = [OSError("storage timeout"), "https://cloud.test/main.jpg"]
        with mock.patch.object(processor, "generate_image", return_value=b"generated") as generate, \
             mock.patch.object(processor, "_ensure_image_meets_spec", return_value=b"jpeg") as normalize, \
             mock.patch.object(processor.time, "sleep"):
            result = processor._gen_main_image(prod, "prompt", storage)
        self.assertEqual(result, "https://cloud.test/main.jpg")
        generate.assert_called_once()
        normalize.assert_called_once_with(b"generated")
        self.assertEqual(storage.upload.call_count, 2)
        self.assertTrue(all(call.args[0] == b"jpeg" for call in storage.upload.call_args_list))
        self.assertNotIn("_main_image_error", prod.result)

    def test_exhausted_uploads_do_not_regenerate_and_expose_stage(self):
        prod = Product(main_img="https://source.test/ref")
        storage = mock.Mock()
        storage.upload.side_effect = OSError("storage unavailable")
        with mock.patch.object(processor, "generate_image", return_value=b"generated") as generate, \
             mock.patch.object(processor, "_ensure_image_meets_spec", return_value=b"jpeg"), \
             mock.patch.object(processor.time, "sleep"):
            result = processor._gen_main_image(prod, "prompt", storage)
        self.assertIsNone(result)
        generate.assert_called_once()
        self.assertEqual(storage.upload.call_count, 3)
        self.assertIn("主图上传", prod.result["_main_image_error"])

    def test_generation_failures_keep_existing_retry_limit_and_redact_errors(self):
        prod = Product(main_img="https://source.test/ref")
        storage = mock.Mock()
        with mock.patch.object(processor, "generate_image", side_effect=OSError(
                "HTTP 403 Bearer private-test-token")) as generate, \
             mock.patch.object(processor.time, "sleep"):
            self.assertIsNone(processor._gen_main_image(prod, "prompt", storage))
        self.assertEqual(generate.call_count, 3)
        storage.upload.assert_not_called()
        self.assertIn("生图", prod.result["_main_image_error"])
        self.assertNotIn("private-test-token", " ".join(prod.logs))

    def test_export_refuses_empty_or_invalid_main_before_mutating_sheet(self):
        for value in (None, "", "bad", "file:///C:/image.jpg", "https:///missing-host", "https://x/a b"):
            with self.subTest(value=value):
                sheet = Workbook().active
                sheet["A8"] = "template sentinel"
                prod = Product(main_img="https://source.test/original.jpg", result={"Z": value})
                with self.assertRaisesRegex(ValueError, "主图"):
                    processor.write_product_row(sheet, 0, prod, {"A": "changed"}, 8)
                self.assertEqual(sheet["A8"].value, "template sentinel")
                self.assertIsNone(sheet["Z8"].value)

    def test_export_uses_valid_generated_url(self):
        sheet = Workbook().active
        prod = Product(main_img="https://source.test/original.jpg", result={"Z": "https://cloud.test/main"})
        processor.write_product_row(sheet, 0, prod, {}, 8)
        self.assertEqual(sheet["Z8"].value, "https://cloud.test/main")

    def test_batch_failed_main_is_not_completed_and_keeps_old_url(self):
        for old_url in (None, "https://cloud.test/old.jpg"):
            with self.subTest(old_url=old_url), ExitStack() as stack:
                prod = Product(parent_sku="P1", result={"Z": old_url})
                app = regen_app([prod])
                mock_phases(stack)
                stack.enter_context(mock.patch("processor._gen_main_image", return_value=None))
                detail = stack.enter_context(mock.patch("processor._gen_detail_html"))
                variants = stack.enter_context(mock.patch("processor._collect_variant_imgs"))
                app._do_batch_regen(["0"])
                app.root.drain()
                self.assertEqual(prod.status, ProductStatus.FAILED)
                self.assertEqual(prod.result["Z"], old_url)
                detail.assert_not_called()
                variants.assert_not_called()
                self.assertIn("失败 1", app._regen_status.configure.call_args.kwargs["text"])

    def test_batch_success_sets_done_and_keeps_selected_preview(self):
        prod, other = Product(parent_sku="P1"), Product(parent_sku="P2")
        app = regen_app([prod, other])
        app._preview_product = other
        with ExitStack() as stack:
            mock_phases(stack)
            stack.enter_context(mock.patch("processor._gen_main_image", return_value="https://cloud.test/main"))
            stack.enter_context(mock.patch("processor._gen_detail_html", return_value="detail"))
            stack.enter_context(mock.patch("processor._collect_variant_imgs", return_value="variants"))
            app._do_batch_regen(["0"])
            app.root.drain()
        self.assertEqual(prod.status, ProductStatus.DONE)
        self.assertEqual(prod.result["Z"], "https://cloud.test/main")
        app._show_preview.assert_not_called()

    def test_manual_regen_failed_callback_keeps_old_url(self):
        prod = Product(result={"Z": "https://cloud.test/old"})
        app = regen_app([prod])
        with ExitStack() as stack:
            mock_phases(stack)
            stack.enter_context(mock.patch("processor._gen_main_image", side_effect=OSError("unavailable")))
            app._do_regen(0, prod)
            app.root.drain()
        self.assertEqual(prod.result["Z"], "https://cloud.test/old")
        self.assertEqual(app._regen_btn.configure.call_args.kwargs["state"], "normal")
        self.assertIn("失败", app._regen_status.configure.call_args.kwargs["text"])

    def test_gui_export_blocks_old_done_state_with_missing_image(self):
        prod = Product(status=ProductStatus.DONE)
        app = regen_app([prod])
        app.template_path = "unused.xlsx"
        app._uses_store_profile = lambda: False
        with mock.patch.object(gui.messagebox, "showerror") as error, \
             mock.patch.object(gui.filedialog, "asksaveasfilename") as save:
            app._do_export()
        save.assert_not_called()
        error.assert_called_once()
        self.assertIn("没有有效主图", error.call_args.args[1])

    def test_gui_export_with_all_failed_does_not_create_template_only_file(self):
        app = regen_app([Product(status=ProductStatus.FAILED)])
        app.template_path = "unused.xlsx"
        app._uses_store_profile = lambda: False
        with mock.patch.object(gui.messagebox, "showinfo") as info, \
             mock.patch.object(gui.filedialog, "asksaveasfilename") as save:
            app._do_export()
        save.assert_not_called()
        self.assertIn("暂无成功完成", info.call_args.args[1])

    def test_completion_dialog_reports_failed_count(self):
        app = regen_app([Product(status=ProductStatus.DONE), Product(status=ProductStatus.FAILED)])
        app._mode_var = mock.Mock()
        app._pause_btn = mock.Mock()
        app._pipeline = mock.Mock(output_path="output.xlsx")
        with mock.patch.object(gui.messagebox, "showinfo") as info:
            app._on_processing_done(2, 1, 1)
        self.assertIn("成功 1 个，失败 1 个", info.call_args.args[1])

    def test_restoring_legacy_done_without_image_marks_failed_for_retry(self):
        bad = Product(parent_sku="bad", status=ProductStatus.DONE)
        good = Product(parent_sku="good", status=ProductStatus.DONE,
                       result={"Z": "https://cloud.test/good"})
        app = regen_app([])
        app.template_path = ""
        app._img_profile_cb = {"values": ()}
        app._title_mode_cb = {"values": ()}
        app._refresh_store_profile_controls = mock.Mock()
        app._update_profile_mode_ui = mock.Mock()
        app._stat_label = mock.Mock()
        app._refresh_list = mock.Mock()
        app._mode_var = mock.Mock()
        app._restore_state({"products": [bad, good]})
        self.assertEqual(bad.status, ProductStatus.FAILED)
        self.assertEqual(good.status, ProductStatus.DONE)
        self.assertTrue(bad.result["_main_image_error"])

    def test_pipeline_catches_worker_error_and_exports_only_successful_products(self):
        products = [Product(parent_sku="bad"), Product(parent_sku="good")]
        pipeline = processor.ProcessingPipeline("source", "template", "output", "generic", True)
        book = mock.Mock()

        def generate(prod, *args):
            if prod.parent_sku == "bad":
                raise OSError("unexpected image error")
            return "https://cloud.test/good"

        def detail(prod, *args):
            self.assertEqual(prod.result["Z"], "https://cloud.test/good")
            return "<P>detail</P>"

        with ExitStack() as stack:
            mock_phases(stack)
            stack.enter_context(mock.patch.object(processor, "init_output_workbook", return_value=(book, object(), {})))
            stack.enter_context(mock.patch.object(processor, "detect_completed", return_value=set()))
            stack.enter_context(mock.patch.object(processor, "_gen_main_image", side_effect=generate))
            stack.enter_context(mock.patch.object(processor, "_gen_detail_html", side_effect=detail))
            stack.enter_context(mock.patch.object(processor, "_collect_variant_imgs", return_value=""))
            write = stack.enter_context(mock.patch.object(processor, "write_product_row"))
            pipeline.run(products)
        self.assertEqual(products[0].status, ProductStatus.FAILED)
        self.assertEqual(products[1].status, ProductStatus.DONE)
        write.assert_called_once()
        self.assertIs(write.call_args.args[2], products[1])
        self.assertEqual(pipeline.errors, [(0, "unexpected image error")])


if __name__ == "__main__":
    unittest.main()
