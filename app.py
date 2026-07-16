from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st
from PIL import Image, ImageChops, ImageDraw, ImageFilter, UnidentifiedImageError
from rembg import new_session, remove


SUPPORTED_TYPES = ["png", "jpg", "jpeg", "webp"]
DPI_VALUE = 300
MODEL_QUALITY = "isnet-general-use"
MODEL_BALANCED = "u2net"
PRESET_ILLUSTRATION = "Ilustrasi putih - kualitas tinggi"
PRESET_STANDARD = "Standard rembg"


@st.cache_resource
def get_rembg_session(model_name: str):
    return new_session(model_name)


def output_name(filename: str) -> str:
    stem = Path(filename).stem or "image"
    return f"{stem}_no_bg_300dpi.png"


def ensure_image(value) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.convert("RGBA")
    return Image.open(BytesIO(value)).convert("RGBA")


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
) -> tuple[Image.Image, Image.Image, bytes]:
    input_image = Image.open(uploaded_file).convert("RGBA")
    model_name = MODEL_QUALITY if preset == PRESET_ILLUSTRATION else MODEL_BALANCED
    session = get_rembg_session(model_name)

    rembg_mask = ensure_image(
        remove(
            input_image,
            session=session,
            only_mask=True,
            post_process_mask=True,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
    ).convert("L")

    if preset == PRESET_ILLUSTRATION:
        white_foreground_mask = build_edge_connected_white_foreground_mask(
            input_image,
            white_tolerance=white_tolerance,
        )
        final_mask = ImageChops.lighter(rembg_mask, white_foreground_mask)
        final_mask = final_mask.filter(ImageFilter.MedianFilter(size=3))
        final_mask = final_mask.filter(ImageFilter.GaussianBlur(radius=0.4))
        output_image = apply_alpha_mask(input_image, final_mask)
    else:
        final_mask = rembg_mask
        output_image = apply_alpha_mask(input_image, final_mask)

    output_buffer = BytesIO()
    output_image.save(output_buffer, format="PNG", dpi=(DPI_VALUE, DPI_VALUE))
    return output_image, final_mask, output_buffer.getvalue()


def build_zip(processed_images: list[dict[str, bytes]]) -> bytes:
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for item in processed_images:
            zip_file.writestr(item["filename"], item["data"])
    return zip_buffer.getvalue()


st.set_page_config(
    page_title="Remove Background",
    layout="wide",
)

st.title("Remove Background")
st.caption("Upload beberapa gambar, hapus background, preview hasil, lalu download PNG 300 DPI.")

settings_col, preview_col = st.columns([1, 1])
with settings_col:
    preset = st.selectbox(
        "Preset",
        options=[PRESET_ILLUSTRATION, PRESET_STANDARD],
        help="Gunakan preset ilustrasi untuk gambar di background putih agar detail bunga/dekorasi tidak mudah terhapus.",
    )

with preview_col:
    show_mask = st.toggle(
        "Preview mask",
        value=False,
        help="Tampilkan area putih sebagai bagian yang dipertahankan dan hitam sebagai background transparan.",
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
)

if not uploaded_files:
    st.info("Upload satu atau lebih gambar untuk mulai.")
    st.stop()

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
        )
    except UnidentifiedImageError:
        st.error(f"{uploaded_file.name} bukan file gambar yang valid.")
        continue
    except Exception as exc:
        st.error(f"Gagal memproses {uploaded_file.name}: {exc}")
        continue

    processed_images.append(
        {
            "filename": output_name(uploaded_file.name),
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
            st.download_button(
                "Download hasil",
                data=output_bytes,
                file_name=output_name(uploaded_file.name),
                mime="image/png",
                key=f"download-{index}-{uploaded_file.name}",
                use_container_width=True,
            )

progress.empty()

if processed_images:
    st.divider()
    st.download_button(
        "Download semua sebagai ZIP",
        data=build_zip(processed_images),
        file_name="removed_backgrounds_300dpi.zip",
        mime="application/zip",
        use_container_width=True,
    )
