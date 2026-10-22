"""Collection controls and autosave for the Streamlit mockup editor."""
from __future__ import annotations

from hashlib import sha256
import json

import streamlit as st

from mockup_store import CollectionConflict, CollectionStore, StorageError


def snapshot_key(backgrounds, points, detections):
    metadata = [(bg["id"], bg["name"], points.get(bg["id"], []), detections.get(bg["id"], {})) for bg in backgrounds]
    return sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()


def activate_collection(code, loaded):
    # Change uploader identities so files from another collection are never re-added.
    for key in list(st.session_state):
        if key.startswith("mockup-") and key not in ("mockup-uploader-epoch",):
            del st.session_state[key]
    st.session_state["mockup-uploader-epoch"] = st.session_state.get("mockup-uploader-epoch", 0) + 1
    records = loaded["backgrounds"]
    st.session_state["mockup-collection-code"] = code
    st.session_state["mockup-collection-revision"] = loaded["revision"]
    st.session_state["mockup-background-records"] = records
    st.session_state["mockup-points"] = {bg["id"]: bg.get("points", []) for bg in records}
    st.session_state["mockup-detections"] = {bg["id"]: bg.get("detection", {"source": "manual", "reason": "Saved corners."}) for bg in records}
    st.session_state["mockup-saved-snapshot"] = snapshot_key(records, st.session_state["mockup-points"], st.session_state["mockup-detections"])


def render_collection_controls():
    store = CollectionStore()
    code = st.session_state.get("mockup-collection-code")
    with st.expander("Koleksi background tersimpan", expanded=not bool(code)):
        if code:
            st.caption("Koleksi aktif. Background dan titik disimpan otomatis di server; painting dan hasil mockup hanya untuk sesi ini.")
            st.download_button("Download kode pribadi", data=code + "\n", file_name="kode_koleksi_mockup.txt", mime="text/plain", key="collection-code-download")
            if st.session_state.get("mockup-new-code"):
                st.code(code, language=None)
                st.caption("Simpan kode ini untuk membuka koleksi lagi. Siapa pun yang memiliki kode dapat membuka dan mengubah koleksinya.")
            if st.button("Muat ulang koleksi dari server", key="collection-reload"):
                try:
                    loaded = store.load(code)
                    activate_collection(code, loaded)
                    st.rerun()
                except (OSError, StorageError, ValueError, KeyError, TypeError):
                    st.error("Koleksi belum bisa dimuat. Pekerjaan dalam sesi tetap tersedia.")
            st.caption("Muat ulang mengganti perubahan dalam sesi dengan versi yang tersimpan di server.")
        else:
            st.caption("Buat koleksi sekali, lalu gunakan kode pribadi yang sama saat membuka app kembali. Tanpa koleksi, pekerjaan hanya bertahan dalam sesi ini.")
        if st.button("Buat koleksi baru", key="collection-create"):
            try:
                new_code = store.create()
                # Keep current work when creating a new collection.
                st.session_state["mockup-collection-code"] = new_code
                st.session_state["mockup-collection-revision"] = 0
                st.session_state["mockup-new-code"] = True
                st.session_state.pop("mockup-saved-snapshot", None)
                st.session_state.pop("mockup-storage-error", None)
                st.session_state.pop("mockup-storage-conflict", None)
                st.session_state["mockup-consumed-uploads"] = set()
                st.rerun()
            except (OSError, StorageError):
                st.error("Koleksi belum bisa dibuat. Periksa lokasi penyimpanan server.")
        with st.form("collection-open-form"):
            entered_code = st.text_input("Kode koleksi pribadi", type="password", key="collection-open-code")
            open_clicked = st.form_submit_button("Buka dengan kode pribadi")
        if open_clicked:
            try:
                entered_code = entered_code.strip()
                loaded = store.load(entered_code)
                activate_collection(entered_code, loaded)
                st.rerun()
            except (OSError, StorageError, ValueError, KeyError, TypeError):
                st.error("Koleksi tidak dapat dibuka. Periksa kode atau penyimpanan server. Koleksi aktif tidak diganti.")
    return store


def autosave_collection(store, backgrounds, points, detections):
    code = st.session_state.get("mockup-collection-code")
    if not code:
        return
    current = snapshot_key(backgrounds, points, detections)
    changed = st.session_state.get("mockup-saved-snapshot") != current
    retry = False
    if st.session_state.get("mockup-storage-error"):
        st.warning("Belum tersimpan: " + st.session_state["mockup-storage-error"])
        if st.session_state.get("mockup-storage-conflict"):
            st.info("Koleksi berubah dari sesi lain. Gunakan Muat ulang koleksi dari server sebelum menyimpan lagi.")
        else:
            retry = st.button("Coba simpan lagi", key="collection-save-retry")
    if (changed and not st.session_state.get("mockup-storage-error")) or retry:
        try:
            revision = store.save(code, backgrounds, points, detections, st.session_state["mockup-collection-revision"])
            st.session_state["mockup-collection-revision"] = revision
            st.session_state["mockup-saved-snapshot"] = current
            st.session_state.pop("mockup-storage-error", None)
            st.session_state.pop("mockup-storage-conflict", None)
        except CollectionConflict as error:
            st.session_state["mockup-storage-error"] = str(error)
            st.session_state["mockup-storage-conflict"] = True
            st.warning("Belum tersimpan: koleksi berubah di sesi lain. Muat ulang dari server untuk melanjutkan.")
        except (OSError, StorageError, ValueError):
            st.session_state["mockup-storage-error"] = "Penyimpanan server gagal. Pekerjaan tetap tersedia dalam sesi; coba simpan lagi."
            st.warning("Belum tersimpan. Pekerjaan tetap tersedia dalam sesi.")
            if not retry:
                st.button("Coba simpan lagi", key="collection-save-retry")
    if st.session_state.get("mockup-saved-snapshot") == current and not st.session_state.get("mockup-storage-error"):
        st.caption("Tersimpan otomatis di koleksi pribadi.")
