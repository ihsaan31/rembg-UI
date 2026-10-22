from __future__ import annotations

import asyncio
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st
from PIL import Image, UnidentifiedImageError

from fal_api import (
    BIREFNET_ENDPOINT,
    BRIA_REMOVE_BACKGROUND_ENDPOINT,
    CRISP_UPSCALE_ENDPOINT,
    PIXELCUT_PRODUCT_PHOTO_ENDPOINT,
    build_request_signature,
    friendly_fal_error,
    normalize_image_to_png,
    run_fal_batch_requests,
    unique_fal_output_names,
)
from mockup_ui import render_painting_mockup
from print_export import PRINT_EXPORT_VERSION, PRINT_SIZES, build_print_zip, prepare_print_source, safe_print_stem


SUPPORTED_TYPES = ["png", "jpg", "jpeg", "webp"]
MAX_FAL_BATCH_FILES = 10
MAX_FAL_CACHED_RESULTS = 20
FAL_BATCH_CONCURRENCY = 2
MAX_UPLOAD_SIZE_MB = 8
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FAL_ACTION_REMOVE = "Remove background"
FAL_ACTION_UPSCALE = "Crisp upscale"
FAL_REMOVE_MODELS = [
    "BRIA RMBG 2.0",
    "BiRefNet v2",
    "Pixelcut Product Photo",
]
FAL_BIREFNET_MODELS = [
    "General Use (Light)",
    "General Use (Light 2K)",
    "General Use (Heavy)",
    "Matting",
    "Portrait",
    "General Use (Dynamic)",
]


def get_uploaded_file_size(uploaded_file) -> int:
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        return int(size)

    current_position = uploaded_file.tell()
    uploaded_file.seek(0, 2)
    size = uploaded_file.tell()
    uploaded_file.seek(current_position)
    return size


def validate_uploaded_files(uploaded_files) -> bool:
    if len(uploaded_files) > MAX_FAL_BATCH_FILES:
        st.error(f"Maximum {MAX_FAL_BATCH_FILES} images per batch.")
        return False

    oversized_files = [
        uploaded_file.name
        for uploaded_file in uploaded_files
        if get_uploaded_file_size(uploaded_file) > MAX_UPLOAD_SIZE_BYTES
    ]
    if oversized_files:
        file_list = ", ".join(oversized_files)
        st.error(f"Files are too large. Maximum {MAX_UPLOAD_SIZE_MB} MB per image: {file_list}")
        return False

    return True


def build_zip(processed_images: list[dict[str, bytes]]) -> bytes:
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for item in processed_images:
            zip_file.writestr(item["filename"], item["data"])
    return zip_buffer.getvalue()


def render_api_settings() -> str:
    with st.sidebar:
        st.header("API Settings")
        api_key = st.text_input(
            "Fal.ai API key",
            type="password",
            key="fal-api-key",
            placeholder="Enter your Fal.ai API key",
            help="The key is only used server-side for this Streamlit session and is not saved to application files.",
        )
        st.caption("The API key is retained only while this application session is active.")
    return api_key


def render_fal_app(api_key: str) -> None:
    st.caption(
        f"Process up to {MAX_FAL_BATCH_FILES} images per batch to remove backgrounds or run Crisp Upscale. "
        "Two images are processed in parallel."
    )

    action = st.radio(
        "Choose an action",
        options=[FAL_ACTION_REMOVE, FAL_ACTION_UPSCALE],
        horizontal=True,
        key="fal-action",
    )

    if action == FAL_ACTION_REMOVE:
        remove_model = st.selectbox(
            "Background remover model",
            options=FAL_REMOVE_MODELS,
            help=(
                "BRIA RMBG 2.0 is the default. BiRefNet v2 provides additional controls, "
                "while Pixelcut is optimized for e-commerce product photos."
            ),
        )

        if remove_model == "BRIA RMBG 2.0":
            st.caption("BRIA RMBG 2.0 removes backgrounds using Bria AI's model.")
            endpoint = BRIA_REMOVE_BACKGROUND_ENDPOINT
            arguments = {"sync_mode": False}
            result_caption = "Background removed with BRIA RMBG 2.0"
            output_suffix = "fal_bria_no_bg"
        elif remove_model == "BiRefNet v2":
            model_col, resolution_col, refine_col = st.columns([1.3, 1, 1])
            with model_col:
                model = st.selectbox(
                    "BiRefNet model",
                    options=FAL_BIREFNET_MODELS,
                    help="General Use (Light) is recommended for most images.",
                )
            resolution_options = ["1024x1024", "2048x2048"]
            if model == "General Use (Dynamic)":
                resolution_options.append("2304x2304")
            with resolution_col:
                operating_resolution = st.selectbox(
                    "Operating resolution",
                    options=resolution_options,
                    help="Higher resolutions may improve detail but use more credits and take longer.",
                )
            with refine_col:
                refine_foreground = st.toggle(
                    "Refine foreground",
                    value=True,
                    help="Improve foreground edges using the estimated mask.",
                )

            endpoint = BIREFNET_ENDPOINT
            arguments = {
                "model": model,
                "operating_resolution": operating_resolution,
                "output_mask": False,
                "refine_foreground": refine_foreground,
                "sync_mode": False,
                "output_format": "png",
                "mask_only": False,
            }
            result_caption = "Background removed with BiRefNet v2"
            output_suffix = "fal_birefnet_no_bg"
        else:
            st.caption(
                "Pixelcut creates a product cutout on a square 1024×1024 canvas "
                "with a transparent background."
            )
            endpoint = PIXELCUT_PRODUCT_PHOTO_ENDPOINT
            arguments = {
                "image_size": "square_hd",
                "background": {"mode": "Transparent"},
                "margin": {"all": "0%"},
                "output_format": "png",
                "sync_mode": False,
            }
            result_caption = "Background removed with Pixelcut"
            output_suffix = "fal_pixelcut_no_bg"
    else:
        st.info("Crisp Upscale improves resolution and fine details. The safety checker remains enabled.")
        endpoint = CRISP_UPSCALE_ENDPOINT
        arguments = {
            "sync_mode": False,
            "enable_safety_checker": True,
        }
        result_caption = "Crisp upscaled"
        output_suffix = "fal_crisp_upscaled"

    uploaded_files = st.file_uploader(
        "Choose images",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        key="fal-uploader",
        help=(
            f"Maximum {MAX_FAL_BATCH_FILES} images. PNG, JPG, JPEG, or WebP, up to "
            f"{MAX_UPLOAD_SIZE_MB} MB per image."
        ),
    )

    if not uploaded_files:
        st.info("Enter an API key and upload one or more images to get started.")
        return

    if not validate_uploaded_files(uploaded_files):
        return

    output_names = unique_fal_output_names(
        [uploaded_file.name for uploaded_file in uploaded_files],
        output_suffix,
    )
    file_records = []
    for index, (uploaded_file, output_filename) in enumerate(
        zip(uploaded_files, output_names),
        start=1,
    ):
        uploaded_bytes = uploaded_file.getvalue()
        file_records.append(
            {
                "index": index,
                "name": uploaded_file.name,
                "input": uploaded_bytes,
                "signature": build_request_signature(
                    uploaded_bytes,
                    f"{action}:{endpoint}",
                    arguments,
                ),
                "output_filename": output_filename,
            }
        )

    result_cache = st.session_state.setdefault("fal-results", {})
    error_cache = st.session_state.setdefault("fal-errors", {})
    pending_records = {}
    for record in file_records:
        if record["signature"] not in result_cache:
            pending_records.setdefault(record["signature"], record)

    can_process = bool(api_key.strip()) and bool(pending_records)
    if not api_key.strip():
        st.warning("Enter a Fal.ai API key to run the process.")
    elif not pending_records:
        st.success("All images in this batch have already been processed.")

    retry_count = sum(signature in error_cache for signature in pending_records)
    if retry_count:
        button_label = f"Retry {len(pending_records)} images"
    elif pending_records:
        button_label = f"Process {len(pending_records)} images with Fal.ai"
    else:
        button_label = "All images are complete"

    if st.button(
        button_label,
        type="primary",
        disabled=not can_process,
        use_container_width=True,
        key="fal-process-batch",
    ):
        status_rows = {}
        valid_items = {}
        total_pending = len(pending_records)
        completed_count = {"value": 0}
        progress = st.progress(0, text=f"Preparing {total_pending} images...")

        for signature, record in pending_records.items():
            error_cache.pop(signature, None)
            try:
                valid_items[signature] = normalize_image_to_png(record["input"])
                status_rows[signature] = st.empty()
                status_rows[signature].info(f'{record["name"]}: waiting for a slot...')
            except (UnidentifiedImageError, OSError):
                error_cache[signature] = "The file could not be read as an image."
                completed_count["value"] += 1
                progress.progress(
                    completed_count["value"] / total_pending,
                    text=f'{record["name"]}: invalid file',
                )

        def on_item_state(signature: str, state: str) -> None:
            record = pending_records[signature]
            if state == "running":
                status_rows[signature].info(f'{record["name"]}: sending to Fal.ai...')
                return

            completed_count["value"] += 1
            progress.progress(
                completed_count["value"] / total_pending,
                text=f'{completed_count["value"]}/{total_pending} images complete',
            )
            if state == "complete":
                status_rows[signature].success(f'{record["name"]}: complete')
            else:
                status_rows[signature].error(f'{record["name"]}: failed')

        def on_queue_update(signature: str, update) -> None:
            logs = getattr(update, "logs", None) or []
            if not logs:
                return
            latest_log = logs[-1]
            message = latest_log.get("message") if isinstance(latest_log, dict) else str(latest_log)
            if message:
                status_rows[signature].info(f'{pending_records[signature]["name"]}: {message}')

        if valid_items:
            outcomes = asyncio.run(
                run_fal_batch_requests(
                    api_key=api_key.strip(),
                    endpoint=endpoint,
                    arguments=arguments,
                    items=valid_items,
                    concurrency=FAL_BATCH_CONCURRENCY,
                    on_queue_update=on_queue_update,
                    on_item_state=on_item_state,
                )
            )
            for signature, outcome in outcomes.items():
                if outcome.result is not None:
                    result_cache[signature] = {
                        "data": outcome.result.data,
                        "content_type": outcome.result.content_type,
                        "endpoint": endpoint,
                        "caption": result_caption,
                    }
                    error_cache.pop(signature, None)
                elif outcome.error is not None:
                    error_cache[signature] = friendly_fal_error(outcome.error)

        current_signatures = {record["signature"] for record in file_records}
        while len(result_cache) > MAX_FAL_CACHED_RESULTS:
            oldest_signature = next(
                (signature for signature in result_cache if signature not in current_signatures),
                None,
            )
            if oldest_signature is None:
                break
            result_cache.pop(oldest_signature)
            error_cache.pop(oldest_signature, None)

        successful_count = sum(record["signature"] in result_cache for record in file_records)
        failed_count = len(
            {
                record["signature"]
                for record in file_records
                if record["signature"] in error_cache
            }
        )
        progress.empty()
        if successful_count:
            st.success(f"{successful_count} images processed successfully.")
        if failed_count:
            st.warning(
                f"{failed_count} images failed. Use the retry button to process only the failed images."
            )

    displayed_errors = set()
    for record in file_records:
        signature = record["signature"]
        if signature in error_cache and signature not in result_cache and signature not in displayed_errors:
            st.error(f'{record["name"]}: {error_cache[signature]}')
            displayed_errors.add(signature)

    processed_images = []
    for record in file_records:
        cached_result = result_cache.get(record["signature"])
        if cached_result is None:
            continue

        st.divider()
        with st.container(border=True):
            st.subheader(record["name"])
            before_col, after_col, action_col = st.columns([1, 1, 0.8])
            with before_col:
                st.image(record["input"], caption="Original", use_container_width=True)
            with after_col:
                st.image(
                    cached_result["data"],
                    caption=cached_result["caption"],
                    use_container_width=True,
                )
            with action_col:
                st.metric("Format", "PNG")
                st.metric("Provider", "Fal.ai")
                st.caption(cached_result["endpoint"])
                st.download_button(
                    "Download result",
                    data=cached_result["data"],
                    file_name=record["output_filename"],
                    mime=cached_result["content_type"],
                    key=f'fal-download-{record["index"]}-{record["signature"][:12]}',
                    use_container_width=True,
                )
        processed_images.append(
            {
                "filename": record["output_filename"],
                "data": cached_result["data"],
            }
        )

    if processed_images:
        st.divider()
        zip_suffix = "removed_backgrounds" if action == FAL_ACTION_REMOVE else "crisp_upscaled"
        st.download_button(
            "Download all results as ZIP",
            data=build_zip(processed_images),
            file_name=f"fal_{zip_suffix}_batch.zip",
            mime="application/zip",
            use_container_width=True,
            key=f"fal-download-zip-{action}",
        )


def render_print_export(api_key: str) -> None:
    st.caption(f"Upload up to {MAX_FAL_BATCH_FILES} images, then click Generate to upscale and export {len(PRINT_SIZES)} master JPG files per image.")
    st.info(
        "Center crop fills each print size. Orientation follows the source image; square images use portrait. "
        "JPG files use 300 DPI pixel dimensions and metadata."
    )
    st.caption(
        "Crisp Upscale runs once per image. Print sizes are then resized with Lanczos; "
        "print detail depends on the source and upscale quality. Transparent areas become white."
    )
    with st.expander(f"Included print sizes ({len(PRINT_SIZES)} master JPG files per image)"):
        for group in ("ISO", "2x3", "3x4", "4x5"):
            labels = [
                f"{size.label} ({size.width:g}×{size.height:g} mm)" if group == "ISO"
                else f"{size.label} {size.unit}"
                for size in PRINT_SIZES if size.group == group
            ]
            st.write(f"{group}: " + ", ".join(labels))

    uploads = st.file_uploader(
        "Choose images for print export", type=SUPPORTED_TYPES, accept_multiple_files=True,
        key="print-uploader", help=f"Maximum {MAX_FAL_BATCH_FILES} images, {MAX_UPLOAD_SIZE_MB} MB each. PNG, JPG, JPEG, or WebP.",
    )
    if not uploads or not validate_uploaded_files(uploads):
        st.session_state.pop("print-batch", None)
        return

    arguments = {"sync_mode": False, "enable_safety_checker": True}
    records = []
    for uploaded in uploads:
        data = uploaded.getvalue()
        signature = build_request_signature(
            data, f"{FAL_ACTION_UPSCALE}:{CRISP_UPSCALE_ENDPOINT}", arguments,
        )
        records.append({"name": uploaded.name, "input": data, "signature": signature})
    batch_key = tuple((record["name"], record["signature"]) for record in records)
    if st.session_state.get("print-batch", {}).get("key") != batch_key:
        st.session_state["print-batch"] = {"key": batch_key, "jobs": {}}
    jobs = st.session_state["print-batch"]["jobs"]
    for job in jobs.values():
        if job.get("export_version") != PRINT_EXPORT_VERSION:
            # Invalidate old packages while retaining the paid upscale result.
            job.pop("zip", None)
    pending = [record for record in records if "zip" not in jobs.get((record["name"], record["signature"]), {})]

    if not api_key.strip():
        st.warning("Enter a Fal.ai API key to generate print files.")
    elif not pending:
        st.success("All print files are ready.")

    if st.button(
        "Generate" if not jobs else "Retry incomplete images",
        key="print-generate", type="primary", use_container_width=True,
        disabled=not api_key.strip() or not pending,
    ):
        cache = st.session_state.setdefault("fal-results", {})
        for record in pending:
            signature = record["signature"]
            job = jobs.setdefault((record["name"], signature), {})
            if "zip" in job:
                continue
            job.pop("error", None)
            progress = st.progress(0, text=f'{record["name"]}: preparing...')
            try:
                png_bytes, landscape = prepare_print_source(record["input"])
                if "upscaled" not in job:
                    cached = cache.get(signature)
                    if cached is None:
                        progress.progress(0, text=f'{record["name"]}: running Crisp Upscale...')
                        outcome = asyncio.run(run_fal_batch_requests(
                            api_key=api_key.strip(), endpoint=CRISP_UPSCALE_ENDPOINT,
                            arguments=arguments, items={signature: png_bytes}, concurrency=1,
                        ))[signature]
                        if outcome.result is None:
                            job["error"] = friendly_fal_error(outcome.error or RuntimeError("Missing result"))
                            continue
                        with Image.open(BytesIO(outcome.result.data)) as result_image:
                            result_image.verify()
                        cached = {
                            "data": outcome.result.data, "content_type": outcome.result.content_type,
                            "endpoint": CRISP_UPSCALE_ENDPOINT, "caption": "Crisp upscaled",
                        }
                        cache[signature] = cached
                        while len(cache) > MAX_FAL_CACHED_RESULTS:
                            cache.pop(next(iter(cache)))
                    job["upscaled"] = cached["data"]

                def update_progress(completed: int, total: int) -> None:
                    progress.progress(completed / total, text=f'{record["name"]}: exporting {completed}/{total} JPG files')

                job["zip"] = build_print_zip(
                    job["upscaled"], record["name"], landscape, on_progress=update_progress,
                )
                job["export_version"] = PRINT_EXPORT_VERSION
            except (UnidentifiedImageError, OSError, ValueError) as error:
                job["error"] = f"Could not prepare or export this image: {error}"
            except Exception:
                job["error"] = "Processing failed. Retry this image; any completed upscale will be reused."
            finally:
                progress.empty()

    for index, record in enumerate(records):
        job = jobs.get((record["name"], record["signature"]), {})
        with st.container(border=True):
            st.subheader(record["name"])
            before, after = st.columns(2)
            with before:
                try:
                    st.image(record["input"], caption="Original", use_container_width=True)
                except (UnidentifiedImageError, OSError, ValueError):
                    st.error("The source file could not be previewed as an image.")
            with after:
                if "upscaled" in job:
                    try:
                        st.image(job["upscaled"], caption="Crisp upscaled", use_container_width=True)
                    except (UnidentifiedImageError, OSError, ValueError):
                        st.error("The upscale result could not be previewed as an image.")
            if "error" in job:
                st.error(job["error"])
            if "zip" in job:
                st.success(f"{len(PRINT_SIZES)} master JPG files ready · 300 DPI · ISO / 2x3 / 3x4 / 4x5")
                st.download_button(
                    "Download print ZIP", data=job["zip"],
                    file_name=f'{safe_print_stem(record["name"])}_print_300dpi.zip',
                    mime="application/zip", key=f"print-download-{index}-{record['signature'][:12]}",
                    use_container_width=True,
                )


st.set_page_config(
    page_title="Fal AI Image Tools",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Fal AI Image Tools")
api_key = render_api_settings()
image_tools_tab, print_tab, mockup_tab = st.tabs(["Image Tools", "Print Size Export", "Painting Mockup"])
with image_tools_tab:
    render_fal_app(api_key)
with print_tab:
    render_print_export(api_key)

with mockup_tab:
    render_painting_mockup()
