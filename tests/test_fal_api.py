from __future__ import annotations

import asyncio
import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fal_api import (  # noqa: E402
    BIREFNET_ENDPOINT,
    FalApiError,
    FalImageResult,
    build_request_signature,
    extract_image_url,
    fal_output_name,
    normalize_image_to_png,
    run_fal_batch_requests,
    run_fal_image_request,
    run_fal_image_request_async,
    unique_fal_output_names,
)


class FakeClient:
    def __init__(self):
        self.uploaded_png = b""
        self.subscription = None

    def upload_file(self, filename):
        self.uploaded_png = Path(filename).read_bytes()
        return "https://uploads.example/input.png"

    def subscribe(self, endpoint, **kwargs):
        self.subscription = (endpoint, kwargs)
        return {"image": {"url": "https://results.example/output.png"}}


class FakeAsyncClient:
    def __init__(self):
        self.uploaded_png = b""
        self.subscription = None

    async def upload_file(self, filename):
        self.uploaded_png = Path(filename).read_bytes()
        return "https://uploads.example/input.png"

    async def subscribe(self, endpoint, **kwargs):
        self.subscription = (endpoint, kwargs)
        return {"image": {"url": "https://results.example/output.png"}}


class FalApiTests(unittest.TestCase):
    def test_normalizes_jpeg_to_png(self):
        source = BytesIO()
        Image.new("RGB", (12, 8), "red").save(source, format="JPEG")

        result = normalize_image_to_png(source.getvalue())

        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.size, (12, 8))
            self.assertEqual(image.format, "PNG")

    def test_signature_changes_with_action_and_arguments(self):
        first = build_request_signature(b"image", "remove", {"model": "light"})
        same = build_request_signature(b"image", "remove", {"model": "light"})
        changed = build_request_signature(b"image", "upscale", {"model": "light"})

        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_runs_one_request_with_expected_endpoint_and_arguments(self):
        client = FakeClient()

        result = run_fal_image_request(
            api_key="secret-key",
            endpoint=BIREFNET_ENDPOINT,
            arguments={"model": "General Use (Light)"},
            png_bytes=b"png-bytes",
            client_factory=lambda key: client if key == "secret-key" else None,
            downloader=lambda url: (b"result-bytes", "image/png"),
        )

        endpoint, kwargs = client.subscription
        self.assertEqual(endpoint, BIREFNET_ENDPOINT)
        self.assertEqual(kwargs["arguments"]["image_url"], "https://uploads.example/input.png")
        self.assertEqual(kwargs["arguments"]["model"], "General Use (Light)")
        self.assertTrue(kwargs["with_logs"])
        self.assertEqual(client.uploaded_png, b"png-bytes")
        self.assertEqual(result.data, b"result-bytes")

    def test_rejects_response_without_image_url(self):
        with self.assertRaises(FalApiError):
            extract_image_url({"image": {}})

    def test_runs_async_request_with_expected_endpoint(self):
        client = FakeAsyncClient()

        async def downloader(url):
            self.assertEqual(url, "https://results.example/output.png")
            return b"async-result", "image/png"

        result = asyncio.run(
            run_fal_image_request_async(
                client=client,
                endpoint=BIREFNET_ENDPOINT,
                arguments={"model": "General Use (Light)"},
                png_bytes=b"async-png",
                downloader=downloader,
            )
        )

        endpoint, kwargs = client.subscription
        self.assertEqual(endpoint, BIREFNET_ENDPOINT)
        self.assertEqual(kwargs["arguments"]["image_url"], "https://uploads.example/input.png")
        self.assertEqual(client.uploaded_png, b"async-png")
        self.assertEqual(result.data, b"async-result")

    def test_batch_limits_concurrency_and_keeps_partial_success(self):
        active = 0
        maximum_active = 0
        states = []

        async def request_runner(**kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            if kwargs["png_bytes"] == b"bad":
                raise RuntimeError("request failed")
            return FalImageResult(b"result", "image/png", "https://results.example/output.png")

        outcomes = asyncio.run(
            run_fal_batch_requests(
                api_key="secret-key",
                endpoint=BIREFNET_ENDPOINT,
                arguments={"model": "General Use (Light)"},
                items={"one": b"first", "two": b"bad", "three": b"third"},
                concurrency=2,
                on_item_state=lambda item_id, state: states.append((item_id, state)),
                client_factory=lambda key: object(),
                request_runner=request_runner,
            )
        )

        self.assertEqual(maximum_active, 2)
        self.assertIsNotNone(outcomes["one"].result)
        self.assertIsInstance(outcomes["two"].error, RuntimeError)
        self.assertIsNotNone(outcomes["three"].result)
        self.assertIn(("two", "error"), states)
        self.assertIn(("three", "complete"), states)

    def test_builds_png_output_name(self):
        self.assertEqual(
            fal_output_name("photo.jpeg", "fal_crisp_upscaled"),
            "photo_fal_crisp_upscaled.png",
        )

    def test_disambiguates_duplicate_output_names(self):
        self.assertEqual(
            unique_fal_output_names(
                ["photo.jpg", "photo.png", "PHOTO.webp"],
                "fal_crisp_upscaled",
            ),
            [
                "photo_fal_crisp_upscaled.png",
                "photo_fal_crisp_upscaled_2.png",
                "PHOTO_fal_crisp_upscaled_3.png",
            ],
        )


if __name__ == "__main__":
    unittest.main()
