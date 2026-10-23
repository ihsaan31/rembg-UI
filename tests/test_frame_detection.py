from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image, ImageDraw
from streamlit.testing.v1 import AppTest

from frame_detection import FrameDetection, detect_frame


def png(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def scene(points=((170, 100), (470, 100), (470, 400), (170, 400)), shadow=False):
    image = Image.new("RGB", (640, 500), "#c8baa0")
    draw = ImageDraw.Draw(image)
    # A thick dark frame with the requested inner quadrilateral.
    draw.polygon([(x + (x-320)*0.08, y + (y-250)*0.08) for x, y in points], fill="#4f3224")
    draw.polygon(points, fill="white")
    if shadow:
        values = np.asarray(image).copy()
        for y in range(values.shape[0]):
            values[y] = (values[y].astype(float) * (0.73 + 0.27*y/values.shape[0])).astype("uint8")
        image = Image.fromarray(values)
    return image


class FrameDetectionTests(unittest.TestCase):
    def test_axis_aligned_and_perspective_with_shadow(self):
        for points, shadow in [(((170, 100), (470, 100), (470, 400), (170, 400)), False), (((200, 70), (490, 140), (430, 420), (140, 360)), True)]:
            with self.subTest(shadow=shadow):
                result = detect_frame(png(scene(points, shadow)))
                self.assertEqual(len(result.points), 4, result.reason)
                np.testing.assert_allclose(result.points, points, atol=3)

    def test_plain_wall_and_divided_window_are_not_frames(self):
        wall = Image.new("RGB", (640, 500), "white")
        self.assertEqual(detect_frame(png(wall)).points, [])
        window = scene()
        draw = ImageDraw.Draw(window)
        draw.line((320, 100, 320, 400), fill="#444444", width=12)
        draw.line((170, 250, 470, 250), fill="#444444", width=12)
        self.assertEqual(detect_frame(png(window)).points, [])

    def test_two_similar_areas_are_ambiguous(self):
        image = Image.new("RGB", (640, 500), "#c8baa0")
        draw = ImageDraw.Draw(image)
        for x in (60, 360):
            draw.rectangle((x-10, 90, x+220, 410), fill="#4f3224")
            draw.rectangle((x, 100, x+210, 400), fill="white")
        result = detect_frame(png(image))
        self.assertEqual(result.points, [])
        self.assertIn("Several", result.reason)

    def test_small_distractor_and_original_resolution(self):
        image = scene()
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 20, 90, 80), fill="white")
        large = image.resize((2560, 2000))
        result = detect_frame(png(large))
        self.assertEqual(len(result.points), 4, result.reason)
        np.testing.assert_allclose(result.points, np.array([[170, 100], [470, 100], [470, 400], [170, 400]]) * 4, atol=8)


class Upload(BytesIO):
    def __init__(self, name, data):
        super().__init__(data)
        self.name = name


class AutoFrameAppTests(unittest.TestCase):
    def setUp(self):
        self.backgrounds = [Upload(f"bg-{i}.png", png(Image.new("RGB", (120, 120), (i*8, 0, 0)))) for i in range(20)]
        self.paintings = []
        self.editor_value = None
        self.canvas_args = {}
        self.corners = [[10, 10], [100, 10], [100, 100], [10, 100]]
        def canvas(**kwargs):
            self.canvas_args = kwargs
            return self.editor_value
        self.upload_patch = patch("streamlit.file_uploader", side_effect=lambda *args, **kwargs: {"mockup-backgrounds": self.backgrounds, "mockup-paintings": self.paintings}.get(kwargs["key"], []))
        self.canvas_patch = patch("mockup_ui.corner_canvas", side_effect=canvas)
        self.upload_patch.start()
        self.canvas_patch.start()
        self.addCleanup(self.upload_patch.stop)
        self.addCleanup(self.canvas_patch.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30)

    def test_twenty_detect_once_skip_failures_and_keep_manual_changes(self):
        outcomes = [FrameDetection(self.corners, "detected") for _ in range(18)] + [FrameDetection(), RuntimeError("failure")]
        with patch("mockup_ui.detect_frame", side_effect=outcomes) as detector:
            self.app.run()
            self.assertEqual(list(self.app.exception), [])
            self.assertEqual(detector.call_count, 20)
            self.assertTrue(any("18/20 backgrounds ready" in caption.value for caption in self.app.caption))
            self.assertTrue(self.app.button(key="mockup-generate").disabled)
            # Uploading a painting reuses the detections made before any painting existed.
            self.paintings = [Upload("art.png", png(Image.new("RGB", (20, 20), "blue")))]
            self.app.run()
            self.assertEqual(detector.call_count, 20)
            self.assertFalse(self.app.button(key="mockup-generate").disabled)
            self.app.button(key="mockup-generate").click().run()
            self.assertEqual(list(self.app.exception), [])
            results = self.app.session_state["mockup-results"]
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0]["entries"]), 18)
            self.assertEqual(len(self.app.get("download_button")), 20)
            # Select the first image explicitly: initial selection now prioritizes failures.
            self.app.selectbox(key="mockup-selected-background").select(0).run()
            # A manual adjustment invalidates old output, then survives revisiting the editor.
            edited = [[11, 11], [99, 11], [99, 99], [11, 99]]
            self.editor_value = {"identity": self.canvas_args["identity"], "event": "edit-1", "points": edited}
            self.app.run()
            first_id = sha256(self.backgrounds[0].getvalue()).hexdigest()
            self.assertEqual(self.app.session_state["mockup-points"][first_id], edited)
            self.assertEqual(len(self.app.get("download_button")), 0)
            self.app.selectbox(key="mockup-selected-background").select(1).run()
            self.app.selectbox(key="mockup-selected-background").select(0).run()
            self.assertEqual(self.app.session_state["mockup-points"][first_id], edited)
            self.assertEqual(detector.call_count, 20)

    def test_redetect_ignores_stale_canvas_event_and_can_recover(self):
        self.backgrounds = self.backgrounds[:1]
        with patch("mockup_ui.detect_frame", side_effect=[FrameDetection(self.corners), FrameDetection([[20, 20], [90, 20], [90, 90], [20, 90]])]):
            self.app.run()
            self.editor_value = {"identity": self.canvas_args["identity"], "event": "old", "points": []}
            self.app.run()
            self.app.button(key="mockup-redetect").click().run()
            self.assertEqual(list(self.app.exception), [])
            bg_id = sha256(self.backgrounds[0].getvalue()).hexdigest()
            self.assertEqual(self.app.session_state["mockup-points"][bg_id][0], [20, 20])
            self.app.run()
            self.assertEqual(self.app.session_state["mockup-points"][bg_id][0], [20, 20])


if __name__ == "__main__":
    unittest.main()
