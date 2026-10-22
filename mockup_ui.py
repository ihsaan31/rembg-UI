"""Streamlit interface for local painting mockups."""
from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image, ImageDraw, UnidentifiedImageError
import streamlit as st
import streamlit.components.v1 as components

from painting_mockup import compose_mockup, load_image, validate_points
from frame_detection import detect_frame
from mockup_collections import render_collection_controls, autosave_collection
from print_export import safe_print_stem

corner_canvas = components.declare_component("painting_corner_canvas", path=str(Path(__file__).parent / "mockup_canvas"))


def read_uploads(uploads, max_files=3):
    if len(uploads) > max_files:
        st.error(f"Maximum {max_files} images per uploader.")
        return None
    records = []
    for index, upload in enumerate(uploads):
        data = upload.getvalue()
        if len(data) > 8 * 1024 * 1024:
            st.error(f"{upload.name}: maximum 8 MB per image.")
            return None
        try:
            image = load_image(data)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            st.error(f"{upload.name}: cannot read image, or image exceeds 24 megapixels.")
            return None
        records.append({"name": upload.name, "data": data, "id": sha256(data).hexdigest(), "size": image.size, "index": index})
    return records


def correction_indices(backgrounds, saved_points):
    pending = []
    for index, bg in enumerate(backgrounds):
        try:
            validate_points(saved_points.get(bg["id"], []), bg["size"])
        except ValueError:
            pending.append(index)
    return pending


def advance_correction():
    pending = st.session_state.get("mockup-correction-order", [])
    current = st.session_state.get("mockup-selected-background", 0)
    others = [index for index in pending if index != current]
    if others:
        st.session_state["mockup-selected-background"] = next((index for index in others if index > current), others[0])


def remove_saved_background(identifier):
    st.session_state["mockup-background-records"] = [
        bg for bg in st.session_state.get("mockup-background-records", []) if bg["id"] != identifier
    ]
    st.session_state.pop("mockup-delete-pending", None)


def cancel_background_removal():
    st.session_state.pop("mockup-delete-pending", None)


def render_painting_mockup():
    st.caption("Upload up to 20 backgrounds and 3 paintings to create every combination locally. No API key needed.")
    st.info("Blank white frame interiors are detected automatically. Review the outlined areas below; open Correct frame corners if needed. Paintings use a center crop and transparent areas become white.")
    store = render_collection_controls()
    uploader_epoch = st.session_state.get("mockup-uploader-epoch", 0)
    upload_suffix = f"-{uploader_epoch}" if uploader_epoch else ""
    left, right = st.columns(2)
    with left:
        background_uploads = st.file_uploader("Background images", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, key=f"mockup-backgrounds{upload_suffix}", help="Up to 20 images, 8 MB and 24 megapixels each.")
    with right:
        painting_uploads = st.file_uploader("Painting images", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, key=f"mockup-paintings{upload_suffix}", help="Up to 3 images, 8 MB and 24 megapixels each.")
    uploads = read_uploads(background_uploads or [], max_files=20)
    if st.session_state.get("mockup-collection-code"):
        backgrounds = st.session_state.setdefault("mockup-background-records", [])
        consumed = st.session_state.setdefault("mockup-consumed-uploads", set())
        if uploads is not None:
            upload_ids = {bg["id"] for bg in uploads}
            consumed.intersection_update(upload_ids)
            known_ids = {bg["id"] for bg in backgrounds}
            rejected = []
            for bg in uploads:
                if bg["id"] not in consumed and bg["id"] not in known_ids:
                    if len(backgrounds) < 20:
                        backgrounds.append(bg)
                        known_ids.add(bg["id"])
                    else:
                        rejected.append(bg["name"])
            consumed.update(upload_ids)
            if rejected:
                st.error("Koleksi maksimal 20 background. Tidak ditambahkan: " + ", ".join(rejected))
    else:
        backgrounds = uploads
        if backgrounds is not None:
            st.session_state["mockup-background-records"] = backgrounds
    if backgrounds is not None:
        for index, bg in enumerate(backgrounds):
            bg["index"] = index
    paintings = read_uploads(painting_uploads or [])
    saved_points = st.session_state.setdefault("mockup-points", {})
    detections = st.session_state.setdefault("mockup-detections", {})
    editor_events = st.session_state.setdefault("mockup-editor-events", {})
    editor_versions = st.session_state.setdefault("mockup-editor-versions", {})
    if backgrounds is not None:
        active_ids = {record["id"] for record in backgrounds}
        for state in (saved_points, detections, editor_events, editor_versions):
            for old_id in list(state):
                if old_id not in active_ids:
                    del state[old_id]
    if not backgrounds or paintings is None:
        st.session_state.pop("mockup-results", None)
        st.session_state.pop("mockup-result-key", None)
        if backgrounds is not None:
            autosave_collection(store, backgrounds, saved_points, detections)
        return

    pending = [bg for bg in backgrounds if bg["id"] not in detections]
    if pending:
        with st.spinner("Detecting blank frame interiors..."):
            for bg in pending:
                if bg["id"] in detections:
                    continue
                # Preserve any corners already set in this session.
                if saved_points.get(bg["id"]):
                    detections[bg["id"]] = {"source": "manual", "reason": "Previously selected corners."}
                    continue
                try:
                    detection = detect_frame(bg["data"])
                    saved_points[bg["id"]] = detection.points
                    detections[bg["id"]] = {"source": "auto", "reason": detection.reason}
                except Exception:
                    saved_points[bg["id"]] = []
                    detections[bg["id"]] = {"source": "auto", "reason": "Detection failed for this image. Mark its corners manually."}

    # Render the editor before calculating readiness so changes apply in this rerun.
    overview = st.container()
    selection_batch = tuple((bg["id"], bg["name"]) for bg in backgrounds)
    pending_corrections = correction_indices(backgrounds, saved_points)
    if st.session_state.get("mockup-selection-batch") != selection_batch:
        st.session_state["mockup-selected-background"] = pending_corrections[0] if pending_corrections else 0
        st.session_state["mockup-selection-batch"] = selection_batch
    pending_set = set(pending_corrections)
    # Keep widget options in the same order throughout this batch. Dynamic labels
    # indicate readiness without resetting the selected image during a correction.
    if st.session_state.get("mockup-order-batch") != selection_batch:
        st.session_state["mockup-background-order"] = pending_corrections + [i for i in range(len(backgrounds)) if i not in pending_set]
        st.session_state["mockup-order-batch"] = selection_batch
    with st.expander("Correct frame corners", expanded=False):
        st.caption("Drag a numbered corner to adjust it. Reset to mark four new corners clockwise: top left → top right → bottom right → bottom left.")
        summary = st.empty()
        selected = st.selectbox("Set frame corners for background", options=st.session_state["mockup-background-order"], format_func=lambda i: f'#{i+1} · {"Perlu koreksi" if i in pending_set else "Siap"} · {backgrounds[i]["name"]}', key="mockup-selected-background")
        record = backgrounds[selected]
        record_id = record["id"]
        if st.button("Detect this frame again", key="mockup-redetect"):
            try:
                detection = detect_frame(record["data"])
                saved_points[record_id] = detection.points
                detections[record_id] = {"source": "auto", "reason": detection.reason}
            except Exception:
                saved_points[record_id] = []
                detections[record_id] = {"source": "auto", "reason": "Detection failed. Mark the corners manually."}
            editor_versions[record_id] = editor_versions.get(record_id, 0) + 1
        identity = f'{record_id}:{editor_versions.get(record_id, 0)}'
        image = load_image(record["data"])
        # Full-resolution editor source; the canvas renders only its visible viewport.
        source = Image.new("RGB", image.size, "white")
        source.paste(image, mask=image.getchannel("A"))
        preview = BytesIO()
        source.save(preview, format="JPEG", quality=95)
        value = corner_canvas(
            identity=identity, background_id=record_id, image="data:image/jpeg;base64," + base64.b64encode(preview.getvalue()).decode(),
            width=record["size"][0], height=record["size"][1], points=saved_points.get(record_id, []),
            key=f'mockup-corners-{record_id}', default=None,
        )
        if value and value.get("identity") == identity and value.get("event") != editor_events.get(record_id):
            saved_points[record_id] = value.get("points", [])
            editor_events[record_id] = value["event"]
            detections[record_id] = {"source": "manual", "reason": "Corners adjusted manually."}
        try:
            validate_points(saved_points.get(record_id, []), record["size"])
        except ValueError as error:
            st.warning(str(error))
        pending_corrections = correction_indices(backgrounds, saved_points)
        st.session_state["mockup-correction-order"] = pending_corrections
        if pending_corrections:
            summary.info("Perlu koreksi: " + ", ".join(f"#{i+1}" for i in pending_corrections))
        else:
            summary.success("Semua background siap")
        st.button("Koreksi berikutnya", key="mockup-next-correction", on_click=advance_correction,
                  disabled=not any(i != selected for i in pending_corrections))

    autosave_collection(store, backgrounds, saved_points, detections)

    ready_backgrounds = []
    with overview:
        columns = st.columns(3)
        for index, bg in enumerate(backgrounds):
            points = saved_points.get(bg["id"], [])
            try:
                validate_points(points, bg["size"])
                ready_backgrounds.append(bg)
                status = "Terdeteksi" if detections[bg["id"]]["source"] == "auto" else "Siap · koreksi manual"
                valid = True
            except ValueError:
                status = "Perlu koreksi"
                valid = False
            thumbnail = load_image(bg["data"])
            thumbnail.thumbnail((420, 320), Image.Resampling.LANCZOS)
            if valid:
                sx, sy = thumbnail.width / bg["size"][0], thumbnail.height / bg["size"][1]
                outline = [(x * sx, y * sy) for x, y in points]
                draw = ImageDraw.Draw(thumbnail)
                draw.line(outline + outline[:1], fill="#00b6db", width=3)
                for number, (x, y) in enumerate(outline, 1):
                    draw.ellipse((x-8, y-8, x+8, y+8), fill="#087a98")
                    draw.text((x-3, y-6), str(number), fill="white")
            with columns[index % 3]:
                st.image(thumbnail, caption=f'{index+1}. {bg["name"]} · {status}', use_container_width=True)
                if not valid:
                    st.caption(detections[bg["id"]]["reason"])
                if st.session_state.get("mockup-collection-code"):
                    if st.button(f"Hapus #{index+1}", key=f'mockup-delete-{bg["id"]}'):
                        st.session_state["mockup-delete-pending"] = bg["id"]
                    if st.session_state.get("mockup-delete-pending") == bg["id"]:
                        st.warning("Hapus background ini dari koleksi tersimpan?")
                        st.button("Ya, hapus", key=f'mockup-confirm-delete-{bg["id"]}',
                                  on_click=remove_saved_background, args=(bg["id"],))
                        st.button("Batal", key=f'mockup-cancel-delete-{bg["id"]}', on_click=cancel_background_removal)
    skipped = len(backgrounds) - len(ready_backgrounds)
    if skipped:
        st.warning(f"{skipped} background(s) need correction and will be skipped. Open Correct frame corners to adjust them.")
    st.caption(f"{len(ready_backgrounds)}/{len(backgrounds)} backgrounds ready. Review the outlines before generating.")
    if not paintings:
        st.info("Upload paintings to generate mockups. Frame selections are retained.")
    batch_key = repr(([(r["name"], r["id"], saved_points.get(r["id"], [])) for r in backgrounds], [(r["name"], r["id"]) for r in paintings]))
    if st.session_state.get("mockup-result-key") != batch_key:
        st.session_state.pop("mockup-results", None)
    total = len(ready_backgrounds) * len(paintings)
    if st.button(f"Generate {total} Mockups", disabled=not ready_backgrounds or not paintings, type="primary", key="mockup-generate"):
        results = []
        progress = st.progress(0, text="Creating mockups...")
        for bg in ready_backgrounds:
            for art in paintings:
                filename = f'{bg["index"]+1:02d}_{safe_print_stem(bg["name"])}__{art["index"]+1:02d}_{safe_print_stem(art["name"])}.png'
                try:
                    data = compose_mockup(bg["data"], art["data"], saved_points[bg["id"]])
                    results.append({"filename": filename, "data": data, "label": f'{bg["name"]} × {art["name"]}'})
                except Exception:
                    results.append({"filename": filename, "error": f'Could not create {bg["name"]} × {art["name"]}. Try again.'})
                progress.progress(len(results) / total, text=f"{len(results)}/{total} mockups processed")
        progress.empty()
        st.session_state["mockup-result-key"] = batch_key
        st.session_state["mockup-results"] = results
    results = st.session_state.get("mockup-results", [])
    successful = []
    for index, result in enumerate(results):
        if "error" in result:
            st.error(result["error"])
            continue
        successful.append(result)
        with st.container(border=True):
            st.image(result["data"], caption=result["label"], use_container_width=True)
            st.download_button("Download mockup", data=result["data"], file_name=result["filename"], mime="image/png", key=f"mockup-download-{index}")
    if successful:
        archive = BytesIO()
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
            for result in successful:
                output.writestr(result["filename"], result["data"])
        st.download_button("Download all mockups as ZIP", data=archive.getvalue(), file_name="painting_mockups.zip", mime="application/zip", key="mockup-zip")
