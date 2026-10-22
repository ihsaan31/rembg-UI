from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fal_api import FalBatchOutcome, FalImageResult, normalize_image_to_png
from print_export import PRINT_EXPORT_VERSION, PRINT_SIZES, PrintSize, build_print_zip, prepare_print_source


def image_bytes(size=(20, 30), color="red", mode="RGB"):
    output = BytesIO()
    Image.new(mode, size, color).save(output, format="PNG")
    return output.getvalue()


class PrintExportTests(unittest.TestCase):
    def test_catalog_has_exact_requested_sizes_and_pixel_dimensions(self):
        self.assertEqual(Counter(size.group for size in PRINT_SIZES), {"ISO": 1, "2x3": 1, "3x4": 1, "4x5": 1})
        expected = {
            ("ISO", "mm"): [(594, 841)],
            ("2x3", "in"): [(24, 36)],
            ("3x4", "in"): [(24, 32)],
            ("4x5", "in"): [(24, 30)],
        }
        for key, dimensions in expected.items():
            self.assertEqual([(s.width, s.height) for s in PRINT_SIZES if (s.group, s.unit) == key], dimensions)
        for size in PRINT_SIZES:
            scale = {"mm": 300 / 25.4, "cm": 300 / 2.54, "in": 300}[size.unit]
            expected_pixels = (round(size.width * scale), round(size.height * scale))
            self.assertEqual(size.pixels(), expected_pixels)
            self.assertEqual(size.pixels(True), expected_pixels[::-1])
        self.assertEqual(next(s for s in PRINT_SIZES if s.label == "24x36").pixels(), (7200, 10800))
        self.assertEqual([s.pixels() for s in PRINT_SIZES], [(7016, 9933), (7200, 10800), (7200, 9600), (7200, 9000)])

    def test_orientation_and_exif_are_applied_before_upscale(self):
        for dimensions, landscape in [((20, 10), True), ((10, 20), False), ((10, 10), False)]:
            _, actual = prepare_print_source(image_bytes(dimensions))
            self.assertEqual(actual, landscape)
        source = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (30, 20), "red").save(source, format="JPEG", exif=exif)
        data, landscape = prepare_print_source(source.getvalue())
        self.assertFalse(landscape)
        for normalized in (data, normalize_image_to_png(source.getvalue())):
            with Image.open(BytesIO(normalized)) as result:
                self.assertEqual(result.size, (20, 30))
                self.assertNotIn(274, result.getexif())

    def test_zip_jpegs_have_dimensions_dpi_white_alpha_and_safe_names(self):
        sizes = [PrintSize("4x5", "0.4x0.5", 0.4, 0.5, "in")]
        for landscape, dimensions in [(False, (120, 150)), (True, (150, 120))]:
            data = build_print_zip(image_bytes(color=(0, 0, 0, 0), mode="RGBA"), "../../poster.png", landscape, sizes=sizes)
            with ZipFile(BytesIO(data)) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                name = archive.namelist()[0]
                self.assertTrue(name.startswith("poster_4x5_"))
                self.assertNotIn("/", name)
                self.assertTrue(name.endswith("in_300dpi.jpg"))
                self.assertEqual(name, f"poster_4x5_{'0.5x0.4' if landscape else '0.4x0.5'}in_300dpi.jpg")
                with Image.open(BytesIO(archive.read(name))) as result:
                    self.assertEqual(result.format, "JPEG")
                    self.assertEqual(result.mode, "RGB")
                    self.assertEqual(result.size, dimensions)
                    self.assertEqual(result.info["dpi"], (300, 300))
                    self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))

    def test_center_crop_preserves_center_without_stretching(self):
        source = Image.new("RGB", (300, 100), "red")
        source.paste("blue", (100, 0, 200, 100))
        output = BytesIO()
        source.save(output, format="PNG")
        data = build_print_zip(output.getvalue(), "art.png", False, sizes=[PrintSize("test", "square", 1/3, 1/3, "in")])
        with ZipFile(BytesIO(data)) as archive:
            with Image.open(BytesIO(archive.read(archive.namelist()[0]))) as result:
                self.assertEqual(result.size, (100, 100))
                for point in [(0, 50), (50, 50), (99, 50)]:
                    red, green, blue = result.getpixel(point)
                    self.assertGreater(blue, 240)
                    self.assertLess(red, 10)

    def test_four_master_entries_and_progress_use_exact_targets(self):
        progress = []
        # Verify every production target without allocating large rasters.
        with patch("print_export.ImageOps.fit", side_effect=lambda *args, **kwargs: Image.new("RGB", (3, 4))) as fit:
            data = build_print_zip(image_bytes(), "poster.png", False, on_progress=lambda n, total: progress.append((n, total)))
        self.assertEqual([call.args[1] for call in fit.call_args_list], [s.pixels() for s in PRINT_SIZES])
        self.assertEqual(progress, [(i, 4) for i in range(1, 5)])
        with ZipFile(BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), [
                "poster_ISO_A1_300dpi.jpg", "poster_2x3_24x36in_300dpi.jpg",
                "poster_3x4_24x32in_300dpi.jpg", "poster_4x5_24x30in_300dpi.jpg",
            ])
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                with Image.open(BytesIO(archive.read(name))) as image:
                    self.assertEqual(image.info["dpi"], (300, 300))


class Upload(BytesIO):
    def __init__(self, name, data):
        super().__init__(data)
        self.name = name
        self.size = len(data)


class PrintAppTests(unittest.TestCase):
    def setUp(self):
        self.uploads = [Upload("poster.png", image_bytes())]
        self.upload_patch = patch("streamlit.file_uploader", side_effect=lambda *args, **kwargs: self.uploads if kwargs["key"] == "print-uploader" else [])
        self.upload_patch.start()
        self.addCleanup(self.upload_patch.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)

    def start(self):
        self.app.run()
        self.app.sidebar.text_input[0].set_value("test-api-key").run()

    def generate(self):
        self.app.button(key="print-generate").click().run()
        self.assertEqual(list(self.app.exception), [])

    def test_generate_caches_rerun_and_resets_downloads_for_new_batch(self):
        calls = []

        async def upscale(**kwargs):
            calls.append(kwargs)
            return {key: FalBatchOutcome(result=FalImageResult(image_bytes(), "image/png", "mock")) for key in kwargs["items"]}

        with patch("fal_api.run_fal_batch_requests", side_effect=upscale), patch("print_export.build_print_zip", return_value=b"zip") as export:
            self.start()
            self.assertEqual(calls, [])
            self.generate()
            self.app.run()
            self.assertEqual(len(calls), 1)
            self.assertEqual(export.call_count, 1)
            self.assertTrue(self.app.button(key="print-generate").disabled)
            self.assertEqual(len(self.app.get("download_button")), 1)
            # A new filename means a new archive, but the same image reuses upscale.
            self.uploads = [Upload("renamed.png", image_bytes())]
            self.app.run()
            self.assertEqual(len(self.app.get("download_button")), 0)
            self.generate()
            self.assertEqual(len(calls), 1)
            self.assertEqual(export.call_count, 2)
            self.uploads = []
            self.app.run()
            self.assertEqual(len(self.app.get("download_button")), 0)

    def test_export_retry_reuses_successful_upscale(self):
        async def upscale(**kwargs):
            return {key: FalBatchOutcome(result=FalImageResult(image_bytes(), "image/png", "mock")) for key in kwargs["items"]}

        with patch("fal_api.run_fal_batch_requests", side_effect=upscale) as api, patch("print_export.build_print_zip", side_effect=[OSError("disk full"), b"zip"]) as export:
            self.start()
            self.generate()
            self.assertTrue(any("disk full" in item.value for item in self.app.error))
            self.generate()
            self.assertEqual(api.call_count, 1)
            self.assertEqual(export.call_count, 2)
            self.assertEqual(len(self.app.get("download_button")), 1)

    def test_old_zip_is_invalidated_without_losing_cached_upscale(self):
        with patch("fal_api.run_fal_batch_requests") as api, patch("print_export.build_print_zip", return_value=b"four masters") as export:
            self.start()
            batch = self.app.session_state["print-batch"]
            record_key = batch["key"][0]
            batch["jobs"][record_key] = {"zip": b"old 35-file zip", "upscaled": image_bytes()}
            self.app.session_state["print-batch"] = batch
            self.app.run()
            self.assertEqual(len(self.app.get("download_button")), 0)
            self.generate()
            self.app.run()
            api.assert_not_called()
            self.assertEqual(export.call_count, 1)
            job = self.app.session_state["print-batch"]["jobs"][record_key]
            self.assertEqual(job["zip"], b"four masters")
            self.assertEqual(job["export_version"], PRINT_EXPORT_VERSION)
            self.assertTrue(any("4 master JPG files ready" in item.value for item in self.app.success))
            self.assertEqual(len(self.app.get("download_button")), 1)

    def test_api_failure_can_retry_without_exporting_failed_result(self):
        attempts = []

        async def upscale(**kwargs):
            attempts.append(1)
            outcome = FalBatchOutcome(error=RuntimeError("402 insufficient credit")) if len(attempts) == 1 else FalBatchOutcome(result=FalImageResult(image_bytes(), "image/png", "mock"))
            return {key: outcome for key in kwargs["items"]}

        with patch("fal_api.run_fal_batch_requests", side_effect=upscale), patch("print_export.build_print_zip", return_value=b"zip") as export:
            self.start()
            self.generate()
            self.assertEqual(export.call_count, 0)
            self.assertTrue(any("kredit" in item.value for item in self.app.error))
            self.generate()
            self.assertEqual(len(attempts), 2)
            self.assertEqual(export.call_count, 1)

    def test_invalid_image_does_not_call_api(self):
        self.uploads = [Upload("broken.png", b"not an image")]
        with patch("fal_api.run_fal_batch_requests") as api:
            self.start()
            self.generate()
            api.assert_not_called()
            self.assertEqual(len(self.app.get("download_button")), 0)

    def test_invalid_api_image_is_not_cached_and_can_retry(self):
        attempts = []

        async def upscale(**kwargs):
            attempts.append(1)
            result = b"invalid result" if len(attempts) == 1 else image_bytes()
            return {key: FalBatchOutcome(result=FalImageResult(result, "image/png", "mock")) for key in kwargs["items"]}

        with patch("fal_api.run_fal_batch_requests", side_effect=upscale), patch("print_export.build_print_zip", return_value=b"zip") as export:
            self.start()
            self.generate()
            self.assertEqual(export.call_count, 0)
            self.generate()
            self.assertEqual(len(attempts), 2)
            self.assertEqual(export.call_count, 1)

    def test_partial_batch_retries_only_failed_image(self):
        self.uploads.append(Upload("second.png", image_bytes(color="blue")))
        requested = []

        async def upscale(**kwargs):
            requested.extend(kwargs["items"])
            failed = len(requested) == 2
            outcome = FalBatchOutcome(error=RuntimeError("timeout")) if failed else FalBatchOutcome(result=FalImageResult(image_bytes(), "image/png", "mock"))
            return {key: outcome for key in kwargs["items"]}

        with patch("fal_api.run_fal_batch_requests", side_effect=upscale), patch("print_export.build_print_zip", return_value=b"zip") as export:
            self.start()
            self.generate()
            self.assertEqual(len(self.app.get("download_button")), 1)
            self.generate()
            self.assertEqual(len(self.app.get("download_button")), 2)
            self.assertEqual(requested, [requested[0], requested[1], requested[1]])
            self.assertEqual(export.call_count, 2)

    def test_upload_limits_prevent_generation(self):
        self.uploads = [Upload(f"{i}.png", image_bytes()) for i in range(11)]
        self.start()
        self.assertTrue(any("Maximum 10" in item.value for item in self.app.error))
        self.assertFalse(any(button.key in ("print-generate", "fal-process-batch") for button in self.app.button))
        self.uploads = [Upload("large.png", image_bytes())]
        self.uploads[0].size = 8 * 1024 * 1024 + 1
        self.app.run()
        self.assertTrue(any("Maximum 8 MB" in item.value for item in self.app.error))
        self.assertFalse(any(button.key in ("print-generate", "fal-process-batch") for button in self.app.button))


class TenImageBatchTests(unittest.TestCase):
    def test_both_tabs_process_ten_retry_failures_and_cache_twenty_results(self):
        for uploader_key, button_key, concurrency, extra_downloads in (
            ("fal-uploader", "fal-process-batch", 2, 1),
            ("print-uploader", "print-generate", 1, 0),
        ):
            with self.subTest(tab=uploader_key):
                uploads = [Upload(f"{i}.png", image_bytes(color=(i, 0, 0))) for i in range(10)]
                requested = []
                failed_once = False

                async def process(**kwargs):
                    nonlocal failed_once
                    self.assertEqual(kwargs["concurrency"], concurrency)
                    outcomes = {}
                    for signature in kwargs["items"]:
                        requested.append(signature)
                        fail = not failed_once
                        failed_once = True
                        callback = kwargs.get("on_item_state")
                        if callback:
                            callback(signature, "running")
                        outcomes[signature] = (
                            FalBatchOutcome(error=RuntimeError("timeout")) if fail else
                            FalBatchOutcome(result=FalImageResult(image_bytes(), "image/png", "mock"))
                        )
                        if callback:
                            callback(signature, "error" if fail else "complete")
                    return outcomes

                with patch("streamlit.file_uploader", side_effect=lambda *args, **kwargs: uploads if kwargs["key"] == uploader_key else []), patch("fal_api.run_fal_batch_requests", side_effect=process), patch("print_export.build_print_zip", return_value=b"zip"):
                    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
                    app.sidebar.text_input[0].set_value("test-api-key").run()
                    app.button(key=button_key).click().run()
                    self.assertEqual(list(app.exception), [])
                    self.assertEqual(len(requested), 10)
                    self.assertEqual(len(app.get("download_button")), 9 + extra_downloads)
                    app.button(key=button_key).click().run()
                    app.run()
                    self.assertEqual(list(app.exception), [])
                    self.assertEqual(requested[-1], requested[0])
                    self.assertEqual(len(requested), 11)
                    self.assertEqual(len(app.get("download_button")), 10 + extra_downloads)
                    self.assertTrue(app.button(key=button_key).disabled)
                    self.assertEqual(len(app.session_state["fal-results"]), 10)
                    # A second distinct batch also remains cached in the session.
                    uploads = [Upload(f"next-{i}.png", image_bytes(color=(i, 100, 0))) for i in range(10)]
                    app.run()
                    app.button(key=button_key).click().run()
                    app.run()
                    self.assertEqual(list(app.exception), [])
                    self.assertEqual(len(requested), 21)
                    self.assertEqual(len(app.session_state["fal-results"]), 20)
                    self.assertEqual(len(app.get("download_button")), 10 + extra_downloads)

    def test_both_tabs_reject_eleven_uploads_and_oversized_files_before_api(self):
        for uploader_key in ("fal-uploader", "print-uploader"):
            with self.subTest(tab=uploader_key), patch("fal_api.run_fal_batch_requests") as api:
                uploads = [Upload(f"{i}.png", image_bytes()) for i in range(11)]
                with patch("streamlit.file_uploader", side_effect=lambda *args, **kwargs: uploads if kwargs["key"] == uploader_key else []):
                    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
                    self.assertEqual(list(app.exception), [])
                    self.assertTrue(any("Maximum 10 images" in item.value for item in app.error))
                    self.assertFalse(any(button.key in ("print-generate", "fal-process-batch") for button in app.button))
                    uploads = [Upload("large.png", image_bytes())]
                    uploads[0].size = 8 * 1024 * 1024 + 1
                    app.run()
                    self.assertTrue(any("Maximum 8 MB" in item.value for item in app.error))
                    self.assertFalse(any(button.key in ("print-generate", "fal-process-batch") for button in app.button))
                    api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
