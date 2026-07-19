# Streamlit Remove Background

Frontend sederhana untuk upload banyak gambar, hapus background dengan `rembg`, preview hasil, dan download PNG 300 DPI.

App ini dibuat dengan mode stabil sebagai default agar lebih aman di VPS/Coolify kecil. Batas default:

- Maksimal 3 gambar per batch untuk `rembg`.
- Maksimal 1 gambar per batch untuk `BEN2`.
- Maksimal 8 MB per gambar.
- Inferensi diproses pada sisi panjang maksimal 1600 px, lalu hasil PNG tetap disimpan di ukuran original dengan 300 DPI.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py --server.port=8502 --server.maxUploadSize=32
```

Setelah Streamlit jalan, buka URL lokal yang muncul di terminal.

## Docker

```bash
docker build -t rembg-ui .
docker run --rm -p 8502:8502 rembg-ui
```

## Coolify

Gunakan deployment type `Dockerfile`, port aplikasi `8502`, dan arahkan domain ke service ini.
Streamlit sudah dikonfigurasi untuk listen di `0.0.0.0:8502` lewat `Dockerfile` dan `nixpacks.toml`.

Rekomendasi RAM:

- `rembg` mode Stabil: minimal 2 GB, lebih nyaman 4 GB.
- `BEN2`: gunakan RAM lebih besar dari 4 GB atau proses satu gambar kecil per batch.

Kalau service masih mati mendadak saat generate, biasanya container kena OOM kill. Turunkan "Batas sisi panjang inferensi" di UI atau pakai gambar input yang lebih kecil.
