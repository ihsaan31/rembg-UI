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
        self.assertTrue(any("maksimal 3 gambar" in item.value for item in app.caption))

    def test_dynamic_birefnet_model_enables_2304_resolution(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=20).run()

        model_select = next(item for item in app.selectbox if item.label == "Model BiRefNet")
        model_select.select("General Use (Dynamic)").run()

        resolution_select = next(item for item in app.selectbox if item.label == "Resolusi operasi")
        self.assertEqual(
            resolution_select.options,
            ["1024x1024", "2048x2048", "2304x2304"],
        )


if __name__ == "__main__":
    unittest.main()
