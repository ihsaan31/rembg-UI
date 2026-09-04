from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Awaitable, Callable, Mapping
from urllib.request import Request, urlopen

from PIL import Image


BIREFNET_ENDPOINT = "fal-ai/birefnet/v2"
BRIA_REMOVE_BACKGROUND_ENDPOINT = "fal-ai/bria/background/remove"
PIXELCUT_PRODUCT_PHOTO_ENDPOINT = "pixelcut/product-photo"
CRISP_UPSCALE_ENDPOINT = "fal-ai/recraft/upscale/crisp"


class FalApiError(RuntimeError):
    """Raised when Fal.ai returns an unusable result."""


@dataclass(frozen=True)
class FalImageResult:
    data: bytes
    content_type: str
    source_url: str


@dataclass(frozen=True)
class FalBatchOutcome:
    result: FalImageResult | None = None
    error: Exception | None = None


def normalize_image_to_png(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        image.load()
        has_alpha = image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        )
        normalized = image.convert("RGBA" if has_alpha else "RGB")

    output = BytesIO()
    normalized.save(output, format="PNG")
    return output.getvalue()


def build_request_signature(
    image_bytes: bytes,
    action: str,
    arguments: Mapping[str, Any],
) -> str:
    digest = hashlib.sha256()
    digest.update(image_bytes)
    digest.update(action.encode("utf-8"))
    digest.update(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def create_fal_client(api_key: str):
    import fal_client

    client_class = getattr(fal_client, "SyncClient", None)
    if client_class is None:
        from fal_client.client import SyncClient

        client_class = SyncClient
    return client_class(key=api_key)


def create_async_fal_client(api_key: str):
    import fal_client

    client_class = getattr(fal_client, "AsyncClient", None)
    if client_class is None:
        from fal_client.client import AsyncClient

        client_class = AsyncClient
    return client_class(key=api_key)


def extract_image_url(result: Any) -> str:
    image = result.get("image") if isinstance(result, Mapping) else getattr(result, "image", None)
    if isinstance(image, Mapping):
        url = image.get("url")
    else:
        url = getattr(image, "url", None)

    if not isinstance(url, str) or not url:
        raise FalApiError("Fal.ai tidak mengembalikan URL gambar hasil.")
    return url


def download_image(url: str, timeout: int = 120) -> tuple[bytes, str]:
    if url.startswith("data:"):
        header, encoded = url.split(",", 1)
        content_type = header[5:].split(";", 1)[0] or "image/png"
        return base64.b64decode(encoded), content_type

    request = Request(url, headers={"User-Agent": "fal-image-tools/1.0"})
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type() or "image/png"
        return response.read(), content_type


async def download_image_async(url: str) -> tuple[bytes, str]:
    return await asyncio.to_thread(download_image, url)


def run_fal_image_request(
    api_key: str,
    endpoint: str,
    arguments: Mapping[str, Any],
    png_bytes: bytes,
    on_queue_update: Callable[[Any], None] | None = None,
    client_factory: Callable[[str], Any] = create_fal_client,
    downloader: Callable[[str], tuple[bytes, str]] = download_image,
) -> FalImageResult:
    client = client_factory(api_key)

    with NamedTemporaryFile(suffix=".png") as temporary_file:
        temporary_file.write(png_bytes)
        temporary_file.flush()
        image_url = client.upload_file(temporary_file.name)

    request_arguments = dict(arguments)
    request_arguments["image_url"] = image_url
    result = client.subscribe(
        endpoint,
        arguments=request_arguments,
        with_logs=True,
        on_queue_update=on_queue_update,
    )
    result_url = extract_image_url(result)
    result_bytes, content_type = downloader(result_url)
    if not result_bytes:
        raise FalApiError("File hasil Fal.ai kosong.")

    return FalImageResult(
        data=result_bytes,
        content_type=content_type,
        source_url=result_url,
    )


async def run_fal_image_request_async(
    client: Any,
    endpoint: str,
    arguments: Mapping[str, Any],
    png_bytes: bytes,
    on_queue_update: Callable[[Any], None | Awaitable[None]] | None = None,
    downloader: Callable[[str], Awaitable[tuple[bytes, str]]] = download_image_async,
) -> FalImageResult:
    with NamedTemporaryFile(suffix=".png") as temporary_file:
        temporary_file.write(png_bytes)
        temporary_file.flush()
        image_url = await client.upload_file(temporary_file.name)

    request_arguments = dict(arguments)
    request_arguments["image_url"] = image_url
    result = await client.subscribe(
        endpoint,
        arguments=request_arguments,
        with_logs=True,
        on_queue_update=on_queue_update,
    )
    result_url = extract_image_url(result)
    result_bytes, content_type = await downloader(result_url)
    if not result_bytes:
        raise FalApiError("File hasil Fal.ai kosong.")

    return FalImageResult(
        data=result_bytes,
        content_type=content_type,
        source_url=result_url,
    )


async def run_fal_batch_requests(
    api_key: str,
    endpoint: str,
    arguments: Mapping[str, Any],
    items: Mapping[str, bytes],
    concurrency: int = 2,
    on_queue_update: Callable[[str, Any], None | Awaitable[None]] | None = None,
    on_item_state: Callable[[str, str], None] | None = None,
    client_factory: Callable[[str], Any] = create_async_fal_client,
    request_runner: Callable[..., Awaitable[FalImageResult]] = run_fal_image_request_async,
) -> dict[str, FalBatchOutcome]:
    if concurrency < 1:
        raise ValueError("Concurrency harus minimal 1.")

    client = client_factory(api_key)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_item(item_id: str, png_bytes: bytes) -> tuple[str, FalBatchOutcome]:
        async with semaphore:
            if on_item_state is not None:
                on_item_state(item_id, "running")

            async def queue_callback(update: Any) -> None:
                if on_queue_update is None:
                    return
                callback_result = on_queue_update(item_id, update)
                if inspect.isawaitable(callback_result):
                    await callback_result

            try:
                result = await request_runner(
                    client=client,
                    endpoint=endpoint,
                    arguments=arguments,
                    png_bytes=png_bytes,
                    on_queue_update=queue_callback,
                )
            except Exception as error:
                if on_item_state is not None:
                    on_item_state(item_id, "error")
                return item_id, FalBatchOutcome(error=error)

            if on_item_state is not None:
                on_item_state(item_id, "complete")
            return item_id, FalBatchOutcome(result=result)

    outcomes = await asyncio.gather(
        *(run_item(item_id, png_bytes) for item_id, png_bytes in items.items())
    )
    return dict(outcomes)


def fal_output_name(original_name: str, suffix: str) -> str:
    stem = Path(original_name).stem or "image"
    return f"{stem}_{suffix}.png"


def unique_fal_output_names(original_names: list[str], suffix: str) -> list[str]:
    output_names: list[str] = []
    used_names: set[str] = set()

    for original_name in original_names:
        candidate = fal_output_name(original_name, suffix)
        stem = Path(candidate).stem
        counter = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{counter}.png"
            counter += 1
        used_names.add(candidate.lower())
        output_names.append(candidate)

    return output_names


def friendly_fal_error(error: Exception) -> str:
    message = str(error).lower()
    if "401" in message or "unauthorized" in message or "api key" in message:
        return "API key Fal.ai tidak valid. Periksa key lalu coba lagi."
    if "402" in message or "credit" in message or "balance" in message:
        return "Saldo atau kredit Fal.ai tidak mencukupi untuk menjalankan model ini."
    if "429" in message or "rate limit" in message:
        return "Batas permintaan Fal.ai tercapai. Tunggu sebentar lalu coba lagi."
    if "timeout" in message or "timed out" in message:
        return "Permintaan Fal.ai melewati batas waktu. Coba lagi dengan gambar yang lebih kecil."
    if isinstance(error, FalApiError):
        return str(error)
    return "Fal.ai gagal memproses gambar. Periksa koneksi, saldo akun, lalu coba lagi."
