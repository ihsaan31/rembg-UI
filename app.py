from __future__ import annotations

import asyncio
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st
from PIL import UnidentifiedImageError

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


SUPPORTED_TYPES = ["png", "jpg", "jpeg", "webp"]
MAX_FAL_BATCH_FILES = 3
MAX_FAL_CACHED_RESULTS = 6
FAL_BATCH_CONCURRENCY = 2
MAX_UPLOAD_SIZE_MB = 8
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FAL_ACTION_REMOVE = "Hapus background"
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
        st.error(f"Maksimal {MAX_FAL_BATCH_FILES} gambar per batch.")
        return False

    oversized_files = [
        uploaded_file.name
        for uploaded_file in uploaded_files
        if get_uploaded_file_size(uploaded_file) > MAX_UPLOAD_SIZE_BYTES
    ]
    if oversized_files:
        file_list = ", ".join(oversized_files)
        st.error(f"File terlalu besar. Maksimal {MAX_UPLOAD_SIZE_MB} MB per gambar: {file_list}")
        return False

    return True


def build_zip(processed_images: list[dict[str, bytes]]) -> bytes:
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for item in processed_images:
            zip_file.writestr(item["filename"], item["data"])
    return zip_buffer.getvalue()


def render_fal_app() -> None:
    st.caption(
        "Proses maksimal 3 gambar sekaligus untuk menghapus background atau menjalankan Crisp Upscale. "
        "Dua gambar diproses paralel."
    )

    with st.sidebar:
        st.header("Pengaturan API")
        api_key = st.text_input(
            "Fal.ai API key",
            type="password",
            key="fal-api-key",
            placeholder="Masukkan API key Fal.ai",
            help="Key hanya digunakan di server untuk sesi Streamlit ini dan tidak disimpan ke file aplikasi.",
        )
        st.caption("API key tetap tersimpan hanya selama sesi aplikasi ini aktif.")

    action = st.radio(
        "Pilih proses",
        options=[FAL_ACTION_REMOVE, FAL_ACTION_UPSCALE],
        horizontal=True,
        key="fal-action",
    )

    if action == FAL_ACTION_REMOVE:
        remove_model = st.selectbox(
            "Model background remover",
            options=FAL_REMOVE_MODELS,
            help=(
                "BRIA RMBG 2.0 menjadi default. BiRefNet v2 menyediakan kontrol tambahan, "
                "sedangkan Pixelcut dioptimalkan untuk foto produk e-commerce."
            ),
        )

        if remove_model == "BRIA RMBG 2.0":
            st.caption("BRIA RMBG 2.0 menghapus background menggunakan model dari Bria AI.")
            endpoint = BRIA_REMOVE_BACKGROUND_ENDPOINT
            arguments = {"sync_mode": False}
            result_caption = "Background removed with BRIA RMBG 2.0"
            output_suffix = "fal_bria_no_bg"
        elif remove_model == "BiRefNet v2":
            model_col, resolution_col, refine_col = st.columns([1.3, 1, 1])
            with model_col:
                model = st.selectbox(
                    "Model BiRefNet",
                    options=FAL_BIREFNET_MODELS,
                    help="General Use (Light) direkomendasikan untuk sebagian besar gambar.",
                )
            resolution_options = ["1024x1024", "2048x2048"]
            if model == "General Use (Dynamic)":
                resolution_options.append("2304x2304")
            with resolution_col:
                operating_resolution = st.selectbox(
                    "Resolusi operasi",
                    options=resolution_options,
                    help="Resolusi lebih tinggi dapat meningkatkan detail tetapi memakai lebih banyak kredit dan waktu.",
                )
            with refine_col:
                refine_foreground = st.toggle(
                    "Perhalus foreground",
                    value=True,
                    help="Perbaiki tepi foreground menggunakan mask yang diperkirakan.",
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
                "Pixelcut menghasilkan cutout produk pada kanvas persegi 1024×1024 "
                "dengan background transparan."
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
        st.info("Crisp Upscale meningkatkan resolusi dan detail kecil. Safety checker tetap aktif.")
        endpoint = CRISP_UPSCALE_ENDPOINT
        arguments = {
            "sync_mode": False,
            "enable_safety_checker": True,
        }
        result_caption = "Crisp upscaled"
        output_suffix = "fal_crisp_upscaled"

    uploaded_files = st.file_uploader(
        "Pilih gambar",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        key="fal-uploader",
        help=(
            f"Maksimal {MAX_FAL_BATCH_FILES} gambar. PNG, JPG, JPEG, atau WebP dengan ukuran maksimal "
            f"{MAX_UPLOAD_SIZE_MB} MB per gambar."
        ),
    )

    if not uploaded_files:
        st.info("Masukkan API key dan upload satu atau lebih gambar untuk mulai.")
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
        st.warning("Masukkan API key Fal.ai untuk menjalankan proses.")
    elif not pending_records:
        st.success("Semua gambar dalam batch ini sudah selesai diproses.")

    retry_count = sum(signature in error_cache for signature in pending_records)
    if retry_count:
        button_label = f"Coba lagi {len(pending_records)} gambar"
    elif pending_records:
        button_label = f"Proses {len(pending_records)} gambar dengan Fal.ai"
    else:
        button_label = "Semua gambar sudah selesai"

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
        progress = st.progress(0, text=f"Menyiapkan {total_pending} gambar...")

        for signature, record in pending_records.items():
            error_cache.pop(signature, None)
            try:
                valid_items[signature] = normalize_image_to_png(record["input"])
                status_rows[signature] = st.empty()
                status_rows[signature].info(f'{record["name"]}: menunggu slot...')
            except (UnidentifiedImageError, OSError):
                error_cache[signature] = "File tidak dapat dibaca sebagai gambar."
                completed_count["value"] += 1
                progress.progress(
                    completed_count["value"] / total_pending,
                    text=f'{record["name"]}: file tidak valid',
                )

        def on_item_state(signature: str, state: str) -> None:
            record = pending_records[signature]
            if state == "running":
                status_rows[signature].info(f'{record["name"]}: mengirim ke Fal.ai...')
                return

            completed_count["value"] += 1
            progress.progress(
                completed_count["value"] / total_pending,
                text=f'{completed_count["value"]}/{total_pending} gambar selesai',
            )
            if state == "complete":
                status_rows[signature].success(f'{record["name"]}: selesai')
            else:
                status_rows[signature].error(f'{record["name"]}: gagal')

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
            st.success(f"{successful_count} gambar berhasil diproses.")
        if failed_count:
            st.warning(
                f"{failed_count} gambar gagal. Gunakan tombol coba lagi untuk memproses ulang yang gagal saja."
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
                    "Download hasil",
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
            "Download semua hasil sebagai ZIP",
            data=build_zip(processed_images),
            file_name=f"fal_{zip_suffix}_batch.zip",
            mime="application/zip",
            use_container_width=True,
            key=f"fal-download-zip-{action}",
        )


st.set_page_config(
    page_title="Fal AI Image Tools",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Fal AI Image Tools")
render_fal_app()
