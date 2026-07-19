from __future__ import annotations

import gc
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st
import torch
from PIL import Image, ImageChops, ImageDraw, ImageFilter, UnidentifiedImageError
from rembg import new_session, remove


SUPPORTED_TYPES = ["png", "jpg", "jpeg", "webp"]
DPI_VALUE = 300
MAX_BATCH_FILES = 3
MAX_BEN2_BATCH_FILES = 1
MAX_UPLOAD_SIZE_MB = 8
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
DEFAULT_INFERENCE_SIDE = 1600
MODEL_QUALITY = "isnet-general-use"
MODEL_BALANCED = "u2net"
MODEL_BEN2 = "PramaLLC/BEN2"
PRESET_ILLUSTRATION = "Ilustrasi putih - kualitas tinggi"
PRESET_STANDARD = "Standard rembg"
QUALITY_STABLE = "Stabil"
QUALITY_DETAIL = "Detail tinggi"


@st.cache_resource
def get_rembg_session(model_name: str):
    return new_session(model_name)


@st.cache_resource
def get_ben2_model():
    from ben2 import AutoModel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModel.from_pretrained(MODEL_BEN2)
    model.to(device).eval()
    return model, device


def output_name(filename: str, suffix: str = "no_bg") -> str:
    stem = Path(filename).stem or "image"
    return f"{stem}_{suffix}_300dpi.png"


def ensure_image(value) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.convert("RGBA")
    return Image.open(BytesIO(value)).convert("RGBA")


def get_uploaded_file_size(uploaded_file) -> int:
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        return int(size)

    current_position = uploaded_file.tell()
    uploaded_file.seek(0, 2)
    size = uploaded_file.tell()
    uploaded_file.seek(current_position)
    return size


def validate_uploaded_files(uploaded_files, max_files: int) -> bool:
    if len(uploaded_files) > max_files:
        st.error(f"Maksimal {max_files} gambar per batch agar proses tidak kehabisan memori.")
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


def fit_for_inference(image: Image.Image, max_side: int) -> tuple[Image.Image, tuple[int, int]]:
    original_size = image.size
    if max(original_size) <= max_side:
        return image.copy(), original_size

    inference_image = image.copy()
    inference_image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return inference_image, original_size


def resize_mask_to_original(mask: Image.Image, original_size: tuple[int, int]) -> Image.Image:
    if mask.size == original_size:
        return mask
    return mask.resize(original_size, Image.Resampling.LANCZOS)


def cleanup_after_image() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def build_edge_connected_white_foreground_mask(
    image: Image.Image,
    white_tolerance: int,
) -> Image.Image:
    rgb_image = image.convert("RGB")
    threshold = 255 - white_tolerance

    channels = rgb_image.split()
    near_white_masks = [channel.point(lambda value: 255 if value >= threshold else 0) for channel in channels]
    near_white = ImageChops.multiply(ImageChops.multiply(near_white_masks[0], near_white_masks[1]), near_white_masks[2])

    background = near_white.copy()
    pixels = background.load()
    width, height = background.size

    for x in range(width):
        if pixels[x, 0] == 255:
            ImageDraw.floodfill(background, (x, 0), 128, thresh=0)
        if pixels[x, height - 1] == 255:
            ImageDraw.floodfill(background, (x, height - 1), 128, thresh=0)

    for y in range(height):
        if pixels[0, y] == 255:
            ImageDraw.floodfill(background, (0, y), 128, thresh=0)
        if pixels[width - 1, y] == 255:
            ImageDraw.floodfill(background, (width - 1, y), 128, thresh=0)

    connected_background = background.point(lambda value: 255 if value == 128 else 0)
    return ImageChops.invert(connected_background)


def apply_alpha_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    output_image = image.copy()
    output_image.putalpha(mask)
    return output_image


def remove_background(
    uploaded_file,
    preset: str,
    white_tolerance: int,
    quality_mode: str,
    max_inference_side: int,
) -> tuple[Image.Image, Image.Image, bytes]:
    uploaded_file.seek(0)
    input_image = Image.open(uploaded_file).convert("RGBA")
    inference_image, original_size = fit_for_inference(input_image, max_inference_side)
    model_name = MODEL_QUALITY if preset == PRESET_ILLUSTRATION else MODEL_BALANCED
    session = get_rembg_session(model_name)
    use_alpha_matting = quality_mode == QUALITY_DETAIL

    rembg_mask = ensure_image(
        remove(
            inference_image,
            session=session,
            only_mask=True,
            post_process_mask=True,
            alpha_matting=use_alpha_matting,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
    ).convert("L")

    if preset == PRESET_ILLUSTRATION:
        white_foreground_mask = build_edge_connected_white_foreground_mask(
            inference_image,
            white_tolerance=white_tolerance,
        )
        final_mask = ImageChops.lighter(rembg_mask, white_foreground_mask)
        final_mask = final_mask.filter(ImageFilter.MedianFilter(size=3))
        final_mask = final_mask.filter(ImageFilter.GaussianBlur(radius=0.4))
    else:
        final_mask = rembg_mask

    final_mask = resize_mask_to_original(final_mask, original_size)
    output_image = apply_alpha_mask(input_image, final_mask)

    output_buffer = BytesIO()
    output_image.save(output_buffer, format="PNG", dpi=(DPI_VALUE, DPI_VALUE))
    return output_image, final_mask, output_buffer.getvalue()


def remove_background_ben2(uploaded_file) -> tuple[Image.Image, bytes]:
    uploaded_file.seek(0)
    input_image = Image.open(uploaded_file).convert("RGB")
    model, _device = get_ben2_model()

    with torch.inference_mode():
        output_image = model.inference(input_image)

    output_image = ensure_image(output_image)
    output_buffer = BytesIO()
    output_image.save(output_buffer, format="PNG", dpi=(DPI_VALUE, DPI_VALUE))
    return output_image, output_buffer.getvalue()


def build_zip(processed_images: list[dict[str, bytes]]) -> bytes:
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for item in processed_images:
            zip_file.writestr(item["filename"], item["data"])
    return zip_buffer.getvalue()


def render_rembg_tab() -> None:
    settings_col, quality_col, preview_col = st.columns([1, 1, 1])
    with settings_col:
        preset = st.selectbox(
            "Preset",
            options=[PRESET_ILLUSTRATION, PRESET_STANDARD],
            index=1,
            help="Gunakan preset ilustrasi untuk gambar di background putih agar detail bunga/dekorasi tidak mudah terhapus.",
        )

    with quality_col:
        quality_mode = st.radio(
            "Mode kualitas",
            options=[QUALITY_STABLE, QUALITY_DETAIL],
            index=0,
            horizontal=True,
            help="Mode stabil lebih hemat memori. Detail tinggi mengaktifkan alpha matting dan lebih berat untuk VPS kecil.",
        )

    with preview_col:
        show_mask = st.toggle(
            "Preview mask",
            value=False,
            help="Tampilkan area putih sebagai bagian yang dipertahankan dan hitam sebagai background transparan.",
        )

    max_inference_side = st.slider(
        "Batas sisi panjang inferensi",
        min_value=800,
        max_value=2400,
        value=DEFAULT_INFERENCE_SIDE,
        step=100,
        help="Gambar besar diperkecil untuk proses mask lalu hasil mask dikembalikan ke ukuran original.",
    )

    white_tolerance = 18
    if preset == PRESET_ILLUSTRATION:
        white_tolerance = st.slider(
            "Toleransi background putih",
            min_value=1,
            max_value=60,
            value=18,
            help="Naikkan jika background agak abu-abu/krem. Turunkan jika area putih di dalam ilustrasi ikut terbaca sebagai background.",
        )

    uploaded_files = st.file_uploader(
        "Pilih gambar",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        key="rembg-uploader",
    )

    if not uploaded_files:
        st.info("Upload satu atau lebih gambar untuk mulai.")
        return

    if not validate_uploaded_files(uploaded_files, MAX_BATCH_FILES):
        return

    processed_images: list[dict[str, bytes]] = []

    progress = st.progress(0, text="Menyiapkan proses...")
    for index, uploaded_file in enumerate(uploaded_files, start=1):
        progress.progress(
            index / len(uploaded_files),
            text=f"Memproses {uploaded_file.name} ({index}/{len(uploaded_files)})",
        )

        try:
            output_image, mask_image, output_bytes = remove_background(
                uploaded_file,
                preset=preset,
                white_tolerance=white_tolerance,
                quality_mode=quality_mode,
                max_inference_side=max_inference_side,
            )
        except UnidentifiedImageError:
            st.error(f"{uploaded_file.name} bukan file gambar yang valid.")
            cleanup_after_image()
            continue
        except MemoryError:
            st.error(f"Memori tidak cukup saat memproses {uploaded_file.name}. Coba kecilkan gambar atau gunakan mode Stabil.")
            cleanup_after_image()
            continue
        except Exception as exc:
            st.error(f"Gagal memproses {uploaded_file.name}: {exc}")
            cleanup_after_image()
            continue

        filename = output_name(uploaded_file.name)
        processed_images.append(
            {
                "filename": filename,
                "data": output_bytes,
            }
        )

        with st.container(border=True):
            st.subheader(uploaded_file.name)
            if show_mask:
                before_col, after_col, mask_col, action_col = st.columns([1, 1, 1, 0.8])
            else:
                before_col, after_col, action_col = st.columns([1, 1, 0.8])

            with before_col:
                uploaded_file.seek(0)
                st.image(uploaded_file, caption="Original", use_container_width=True)

            with after_col:
                st.image(output_image, caption="Background removed", use_container_width=True)

            if show_mask:
                with mask_col:
                    st.image(mask_image, caption="Mask preview", use_container_width=True)

            with action_col:
                st.metric("Format", "PNG")
                st.metric("DPI", str(DPI_VALUE))
                st.metric("Model", MODEL_QUALITY if preset == PRESET_ILLUSTRATION else MODEL_BALANCED)
                st.metric("Mode", quality_mode)
                st.download_button(
                    "Download hasil",
                    data=output_bytes,
                    file_name=filename,
                    mime="image/png",
                    key=f"rembg-download-{index}-{uploaded_file.name}",
                    use_container_width=True,
                )

        del output_image, mask_image, output_bytes
        cleanup_after_image()

    progress.empty()

    if processed_images:
        st.divider()
        st.download_button(
            "Download semua sebagai ZIP",
            data=build_zip(processed_images),
            file_name="rembg_removed_backgrounds_300dpi.zip",
            mime="application/zip",
            use_container_width=True,
        )


def render_ben2_tab() -> None:
    uploaded_files = st.file_uploader(
        "Pilih gambar",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        key="ben2-uploader",
    )

    if not uploaded_files:
        st.info("Upload satu atau lebih gambar untuk mulai.")
        return

    if not validate_uploaded_files(uploaded_files, MAX_BEN2_BATCH_FILES):
        return

    st.warning("BEN2 memakai model besar. Untuk VPS kecil, proses satu gambar per batch agar service tidak kehabisan memori.")

    processed_images: list[dict[str, bytes]] = []

    progress = st.progress(0, text="Menyiapkan model BEN2...")
    for index, uploaded_file in enumerate(uploaded_files, start=1):
        progress.progress(
            index / len(uploaded_files),
            text=f"Memproses {uploaded_file.name} dengan BEN2 ({index}/{len(uploaded_files)})",
        )

        try:
            output_image, output_bytes = remove_background_ben2(uploaded_file)
        except UnidentifiedImageError:
            st.error(f"{uploaded_file.name} bukan file gambar yang valid.")
            cleanup_after_image()
            continue
        except MemoryError:
            st.error(f"Memori tidak cukup saat memproses {uploaded_file.name} dengan BEN2. Gunakan gambar lebih kecil atau tab rembg mode Stabil.")
            cleanup_after_image()
            continue
        except ModuleNotFoundError as exc:
            st.error(f"Dependency BEN2 belum terpasang: {exc}")
            cleanup_after_image()
            st.stop()
        except Exception as exc:
            st.error(f"Gagal memproses {uploaded_file.name}: {exc}")
            cleanup_after_image()
            continue

        filename = output_name(uploaded_file.name, suffix="ben2_no_bg")
        processed_images.append(
            {
                "filename": filename,
                "data": output_bytes,
            }
        )

        with st.container(border=True):
            st.subheader(uploaded_file.name)
            before_col, after_col, action_col = st.columns([1, 1, 0.8])

            with before_col:
                uploaded_file.seek(0)
                st.image(uploaded_file, caption="Original", use_container_width=True)

            with after_col:
                st.image(output_image, caption="Background removed", use_container_width=True)

            with action_col:
                st.metric("Format", "PNG")
                st.metric("DPI", str(DPI_VALUE))
                st.metric("Model", "BEN2")
                st.metric("Device", "CUDA" if torch.cuda.is_available() else "CPU")
                st.download_button(
                    "Download hasil",
                    data=output_bytes,
                    file_name=filename,
                    mime="image/png",
                    key=f"ben2-download-{index}-{uploaded_file.name}",
                    use_container_width=True,
                )

        del output_image, output_bytes
        cleanup_after_image()

    progress.empty()

    if processed_images:
        st.divider()
        st.download_button(
            "Download semua sebagai ZIP",
            data=build_zip(processed_images),
            file_name="ben2_removed_backgrounds_300dpi.zip",
            mime="application/zip",
            use_container_width=True,
        )


st.set_page_config(
    page_title="Remove Background",
    layout="wide",
)

st.title("Remove Background")
st.caption("Upload beberapa gambar, hapus background, preview hasil, lalu download PNG 300 DPI.")

rembg_tab, ben2_tab = st.tabs(["rembg", "BEN2"])

with rembg_tab:
    render_rembg_tab()

with ben2_tab:
    render_ben2_tab()
