import unittest
import inspect

import gui
from gui import UploaderApp
from models import Product


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeTree:
    def __init__(self):
        self.selected = None

    def selection(self):
        return ("0",)

    def index(self, item):
        return int(item)

    def get_children(self):
        return ("0",)

    def selection_set(self, item):
        self.selected = item


class FakeStyle:
    def __init__(self):
        self.theme = None

    def theme_use(self, name):
        self.theme = name


class FakeRoot:
    def __init__(self):
        self.style = FakeStyle()


class FakeCanvas:
    def __init__(self):
        self.actions = []

    def delete(self, *args):
        self.actions.append(("delete", args))

    def create_text(self, *args, **kwargs):
        self.actions.append(("create_text", args, kwargs))


class PlatformSwitchTests(unittest.TestCase):
    def setUp(self):
        self._orig_load_config = gui.load_config
        self._orig_save_config = gui.save_config

    def tearDown(self):
        gui.load_config = self._orig_load_config
        gui.save_config = self._orig_save_config

    def test_platform_switch_refreshes_selected_preview(self):
        app = UploaderApp.__new__(UploaderApp)
        prod = Product(parent_sku="P1", main_img="main.jpg")
        prod.extra_imgs = ["main.jpg", "extra.jpg"]
        prod.variant_imgs = ["variant-first.jpg", "variant-second.jpg"]
        app.products = [prod]
        app._orig_main_img = {"P1": "main.jpg"}
        app._platform_var = FakeVar("aliexpress")
        app._tree = FakeTree()

        refreshed = []
        previews = []
        app._refresh_list = lambda: refreshed.append(True)
        app._show_preview = lambda p: previews.append(p.main_img)

        app._on_platform_changed()

        self.assertEqual(prod.main_img, "variant-first.jpg")
        self.assertEqual(refreshed, [True])
        self.assertEqual(previews, ["variant-first.jpg"])

    def test_platform_change_saves_config_even_without_products(self):
        app = UploaderApp.__new__(UploaderApp)
        app.products = []
        app._platform_var = FakeVar("aliexpress")
        saved = []
        gui.load_config = lambda: {"image_api": "routeapi"}
        gui.save_config = lambda cfg: saved.append(dict(cfg))

        app._on_platform_changed()

        self.assertEqual(saved, [{"image_api": "routeapi", "platform": "aliexpress"}])

    def test_toolbar_setting_change_saves_config(self):
        app = UploaderApp.__new__(UploaderApp)
        app._img_profile_var = FakeVar("generic")
        saved = []
        gui.load_config = lambda: {"platform": "shein"}
        gui.save_config = lambda cfg: saved.append(dict(cfg))

        app._on_toolbar_setting_changed("image_prompt")

        self.assertEqual(saved, [{"platform": "shein", "image_prompt": "generic"}])

    def test_title_mode_setting_change_saves_config(self):
        app = UploaderApp.__new__(UploaderApp)
        app._title_mode_var = FakeVar("brand_only")
        saved = []
        gui.load_config = lambda: {"platform": "shein"}
        gui.save_config = lambda cfg: saved.append(dict(cfg))

        app._on_toolbar_setting_changed("title_mode")

        self.assertEqual(saved, [{"platform": "shein", "title_mode": "brand_only"}])

    def test_toolbar_exposes_brand_only_title_mode(self):
        source = inspect.getsource(UploaderApp._setup_toolbar)

        self.assertIn("AI重写", source)
        self.assertIn("仅去品牌", source)
        self.assertIn("title_mode", source)

    def test_theme_change_saves_config(self):
        app = UploaderApp.__new__(UploaderApp)
        app.root = FakeRoot()
        saved = []
        gui.load_config = lambda: {"platform": "shein"}
        gui.save_config = lambda cfg: saved.append(dict(cfg))

        app._set_theme("darkly")

        self.assertEqual(app.root.style.theme, "darkly")
        self.assertEqual(saved, [{"platform": "shein", "theme": "darkly"}])

    def test_regenerate_checked_passes_storage_to_variant_uploads(self):
        source = inspect.getsource(UploaderApp._do_batch_regen)

        self.assertIn("_collect_variant_imgs(prod, storage)", source)

    def test_api_settings_only_exposes_tencent_cos_secrets(self):
        source = inspect.getsource(UploaderApp._open_api_settings)

        self.assertIn("Tencent COS SecretId", source)
        self.assertIn("Tencent COS SecretKey", source)
        self.assertNotIn("Tencent COS Bucket", source)
        self.assertNotIn("Tencent COS Region", source)
        self.assertNotIn("Tencent COS Prefix", source)
        self.assertNotIn("Tencent COS Base URL", source)
        self.assertIn('"tencent_cos"', source)

    def test_main_image_source_text_reports_platform_rule(self):
        app = UploaderApp.__new__(UploaderApp)
        prod = Product(parent_sku="P1", main_img="main.jpg", platform="shein")
        prod.extra_imgs = ["main.jpg"]
        prod.variant_imgs = ["variant-first.jpg"]

        self.assertIn("Shein 采集表主图列", app._main_image_source_text(prod))
        self.assertIn("AliExpress候选: variant-first.jpg", app._main_image_source_text(prod))

        prod.platform = "aliexpress"
        prod.main_img = "variant-first.jpg"
        self.assertIn("AliExpress 第一张变种图", app._main_image_source_text(prod))
        self.assertIn("Shein主图: main.jpg", app._main_image_source_text(prod))

    def test_platform_switch_uses_extra_images_when_original_map_missing(self):
        app = UploaderApp.__new__(UploaderApp)
        prod = Product(parent_sku="P1", main_img="main.jpg")
        prod.extra_imgs = ["main.jpg", "extra.jpg"]
        prod.variant_imgs = ["variant-first.jpg"]
        app.products = [prod]
        app._platform_var = FakeVar("aliexpress")
        app._tree = FakeTree()
        app._refresh_list = lambda: None
        app._show_preview = lambda p: None

        app._on_platform_changed()

        self.assertEqual(prod.main_img, "variant-first.jpg")

        app._platform_var = FakeVar("shein")
        app._on_platform_changed()

        self.assertEqual(prod.main_img, "main.jpg")

    def test_preview_token_rejects_stale_image_loads(self):
        app = UploaderApp.__new__(UploaderApp)
        app._preview_token = 2

        self.assertTrue(app._is_current_preview(2))
        self.assertFalse(app._is_current_preview(1))

    def test_test_image_error_draws_full_message_on_canvas(self):
        app = UploaderApp.__new__(UploaderApp)
        app._img_result = FakeCanvas()
        app._test_status = type("Status", (), {"configure": lambda self, **kwargs: None})()
        app._test_url_var = type("Var", (), {"set": lambda self, value: None})()
        app._copy_btn = type("Button", (), {"configure": lambda self, **kwargs: None})()
        msg = "no reference images could be downloaded: https://ae-pic-a1.example.com/kf/very-long-url.jpg: HTTP 403"

        app._show_test_error(msg)

        text_actions = [a for a in app._img_result.actions if a[0] == "create_text"]
        self.assertEqual(len(text_actions), 1)
        self.assertIn("very-long-url.jpg", text_actions[0][2]["text"])
        self.assertIn("width", text_actions[0][2])
        self.assertEqual(app._last_test_error, msg)


if __name__ == "__main__":
    unittest.main()
