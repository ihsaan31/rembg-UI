from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.proto.TextInput_pb2 import TextInput
from streamlit.testing.v1 import AppTest


class AppSmokeTests(unittest.TestCase):
    def test_app_renders_fal_only_interface_and_masked_key_input(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"

        app = AppTest.from_file(str(app_path), default_timeout=20).run()

        self.assertEqual(app.exception, [])
        self.assertEqual([title.value for title in app.title], ["Fal AI Image Tools"])
        self.assertEqual(list(app.tabs), [])
        fal_key_input = next(
            item for item in app.sidebar.text_input if item.label == "Fal.ai API key"
        )
        self.assertEqual(fal_key_input.proto.type, TextInput.PASSWORD)
        self.assertTrue(any("up to 3 images" in item.value for item in app.caption))

    def test_dynamic_birefnet_model_enables_2304_resolution(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=20).run()

        remove_model_select = next(
            item for item in app.selectbox if item.label == "Background remover model"
        )
        self.assertEqual(remove_model_select.value, "BRIA RMBG 2.0")
        self.assertFalse(any(item.label == "BiRefNet model" for item in app.selectbox))

        remove_model_select.select("BiRefNet v2").run()
        model_select = next(item for item in app.selectbox if item.label == "BiRefNet model")
        model_select.select("General Use (Dynamic)").run()

        resolution_select = next(
            item for item in app.selectbox if item.label == "Operating resolution"
        )
        self.assertEqual(
            resolution_select.options,
            ["1024x1024", "2048x2048", "2304x2304"],
        )

    def test_bria_is_default_and_hides_birefnet_controls(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=20).run()

        remove_model_select = next(
            item for item in app.selectbox if item.label == "Background remover model"
        )
        self.assertEqual(
            remove_model_select.options,
            ["BRIA RMBG 2.0", "BiRefNet v2", "Pixelcut Product Photo"],
        )
        self.assertEqual(remove_model_select.value, "BRIA RMBG 2.0")
        self.assertFalse(any(item.label == "Operating resolution" for item in app.selectbox))
        self.assertEqual(list(app.toggle), [])

    def test_pixelcut_hides_birefnet_controls(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=20).run()

        remove_model_select = next(
            item for item in app.selectbox if item.label == "Background remover model"
        )
        remove_model_select.select("Pixelcut Product Photo").run()

        self.assertFalse(any(item.label == "BiRefNet model" for item in app.selectbox))
        self.assertFalse(any(item.label == "Operating resolution" for item in app.selectbox))
        self.assertEqual(list(app.toggle), [])
        self.assertTrue(
            any("transparent background" in item.value for item in app.caption)
        )


if __name__ == "__main__":
    unittest.main()
