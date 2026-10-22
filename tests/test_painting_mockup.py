from io import BytesIO
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from painting_mockup import compose_mockup, load_image, validate_points


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class PaintingMockupTests(unittest.TestCase):
    def test_perspective_maps_colored_corners_and_preserves_scene(self):
        art = Image.new("RGB", (100, 100), "red")
        art.paste("green", (50, 0, 100, 50))
        art.paste("blue", (50, 50, 100, 100))
        art.paste("yellow", (0, 50, 50, 100))
        bg = Image.new("RGB", (160, 160), "purple")
        points = [(30, 20), (130, 35), (120, 130), (20, 120)]
        result = load_image(compose_mockup(png(bg), png(art), points))
        for point, color in [((40, 35), (255, 0, 0)), ((115, 45), (0, 128, 0)), ((108, 115), (0, 0, 255)), ((35, 105), (255, 255, 0))]:
            self.assertEqual(result.getpixel(point)[:3], color)
        self.assertEqual(result.size, bg.size)
        for x in range(160):
            self.assertEqual(result.getpixel((x, 0)), (*bg.getpixel((x, 0)), 255))
        self.assertEqual(result.getpixel((150, 80)), (128, 0, 128, 255))

    def test_center_crop_and_transparency(self):
        art = Image.new("RGBA", (300, 100), "red")
        art.paste((0, 0, 0, 0), (100, 0, 200, 100))
        result = load_image(compose_mockup(png(Image.new("RGB", (140, 140), "black")), png(art), [(20, 20), (119, 20), (119, 119), (20, 119)]))
        self.assertEqual(result.getpixel((70, 70)), (255, 255, 255, 255))
        self.assertEqual(result.getpixel((30, 70)), (255, 255, 255, 255))

    def test_invalid_points(self):
        invalid = [[], [(0, 0)]*4, [(10, 10), (90, 90), (90, 10), (10, 90)], [(0, 0), (200, 0), (90, 90), (0, 90)], [(0, 0), (2, 0), (2, 2), (0, 2)], [(0, 0), (90, 0), (20, 20), (0, 90)], [(float("nan"), 0)]*4]
        for points in invalid:
            with self.subTest(points=points), self.assertRaises(ValueError):
                validate_points(points, (100, 100))

    def test_exif_orientation_and_resolution_limit(self):
        output = BytesIO()
        image = Image.new("RGB", (30, 20), "red")
        exif = Image.Exif()
        exif[274] = 6
        image.save(output, format="JPEG", exif=exif)
        self.assertEqual(load_image(output.getvalue()).size, (20, 30))
        with patch("painting_mockup.MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(ValueError):
                load_image(output.getvalue())

class MockupAppTests(unittest.TestCase):
    def test_batch_zip_points_retention_and_result_invalidation(self):
        from hashlib import sha256
        from zipfile import ZipFile
        from streamlit.testing.v1 import AppTest
        import streamlit as st

        class Upload(BytesIO):
            def __init__(self, name, data):
                super().__init__(data)
                self.name = name

        backgrounds = [Upload("bg.png", png(Image.new("RGB", (120, 120), color))) for color in ("purple", "gray")]
        paintings = [Upload("art.png", png(Image.new("RGB", (40, 60), color))) for color in ("red", "green", "blue")]
        points = {sha256(bg.getvalue()).hexdigest(): [(10, 10), (100, 10), (100, 100), (10, 100)] for bg in backgrounds}
        current_points = {"value": None}
        def uploader(*args, **kwargs):
            return {"mockup-backgrounds": backgrounds, "mockup-paintings": paintings}.get(kwargs["key"], [])

        with patch("streamlit.file_uploader", side_effect=uploader), patch("mockup_ui.corner_canvas", side_effect=lambda **kwargs: ({**current_points["value"], "identity": kwargs["identity"], "event": "test-edit"} if current_points["value"] else None)), patch("streamlit.download_button", wraps=st.download_button) as download:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["mockup-points"] = points
            app.run()
            self.assertEqual(list(app.exception), [])
            self.assertFalse(app.button(key="mockup-generate").disabled)
            app.button(key="mockup-generate").click().run()
            self.assertEqual(list(app.exception), [])
            results = app.session_state["mockup-results"]
            self.assertEqual(len(results), 6)
            self.assertEqual(len({item["filename"] for item in results}), 6)
            self.assertEqual(len(app.get("download_button")), 7)
            zip_bytes = next(call.kwargs["data"] for call in download.call_args_list if call.kwargs.get("file_name") == "painting_mockups.zip")
            with ZipFile(BytesIO(zip_bytes)) as archive:
                self.assertEqual(len(archive.namelist()), 6)
                self.assertIsNone(archive.testzip())
            self.assertEqual(app.sidebar.text_input[0].value, "")
            # Changing paintings keeps frame coordinates and removes stale output.
            paintings[0] = Upload("new.png", png(Image.new("RGB", (40, 60), "yellow")))
            app.run()
            self.assertEqual(app.session_state["mockup-points"], points)
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="mockup-generate").click().run()
            current_points["value"] = {"points": []}
            app.run()
            self.assertFalse(app.button(key="mockup-generate").disabled)
            self.assertEqual(len(app.get("download_button")), 0)


if __name__ == "__main__":
    unittest.main()
