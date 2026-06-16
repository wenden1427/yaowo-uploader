import unittest

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

    def test_theme_change_saves_config(self):
        app = UploaderApp.__new__(UploaderApp)
        app.root = FakeRoot()
        saved = []
        gui.load_config = lambda: {"platform": "shein"}
        gui.save_config = lambda cfg: saved.append(dict(cfg))

        app._set_theme("darkly")

        self.assertEqual(app.root.style.theme, "darkly")
        self.assertEqual(saved, [{"platform": "shein", "theme": "darkly"}])

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


if __name__ == "__main__":
    unittest.main()
