# Streamlit Remove Background

Frontend sederhana untuk upload banyak gambar, hapus background dengan `rembg`, preview hasil, dan download PNG 300 DPI.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Setelah Streamlit jalan, buka URL lokal yang muncul di terminal.

## Docker

```bash
docker build -t rembg-ui .
docker run --rm -p 8501:8501 rembg-ui
```

## Coolify

Gunakan deployment type `Dockerfile`, port aplikasi `8501`, dan arahkan domain ke service ini.
Streamlit sudah dikonfigurasi untuk listen di `0.0.0.0:8501` lewat environment variable di Dockerfile.
# rembg-UI
