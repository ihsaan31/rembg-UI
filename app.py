from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st
from PIL import Image, UnidentifiedImageError
from rembg import new_session, remove


SUPPORTED_TYPES = ["png", "jpg", "jpeg", "webp"]
DPI_VALUE = 300


@st.cache_resource
def get_rembg_session():
    return new_session()


def output_name(filename: str) -> str:
    stem = Path(filename).stem or "image"
    return f"{stem}_no_bg_300dpi.png"


def remove_background(uploaded_file) -> tuple[Image.Image, bytes]:
    input_image = Image.open(uploaded_file).convert("RGBA")
    output_image = remove(input_image, session=get_rembg_session())

    if not isinstance(output_image, Image.Image):
        output_image = Image.open(BytesIO(output_image)).convert("RGBA")

    output_buffer = BytesIO()
    output_image.save(output_buffer, format="PNG", dpi=(DPI_VALUE, DPI_VALUE))
    return output_image, output_buffer.getvalue()


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
        output_image, output_bytes = remove_background(uploaded_file)
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
        before_col, after_col, action_col = st.columns([1, 1, 0.8])

        with before_col:
            uploaded_file.seek(0)
            st.image(uploaded_file, caption="Original", use_container_width=True)

        with after_col:
            st.image(output_image, caption="Background removed", use_container_width=True)

        with action_col:
            st.metric("Format", "PNG")
            st.metric("DPI", str(DPI_VALUE))
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
