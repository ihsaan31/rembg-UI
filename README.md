# Fal AI Image Tools

Streamlit UI untuk menghapus background dan upscale gambar melalui Fal.ai. Aplikasi ini sepenuhnya memakai API dan tidak mengunduh atau menjalankan model AI lokal.

## Fitur

- Remove background melalui `fal-ai/birefnet/v2`.
- Crisp Upscale melalui `fal-ai/recraft/upscale/crisp`.
- Maksimal 3 gambar per batch dan 8 MB per gambar.
- Maksimal 2 request Fal.ai berjalan paralel.
- Retry hanya untuk gambar yang gagal.
- Preview, download individual, dan download seluruh hasil sebagai ZIP.

Fal.ai API key dimasukkan melalui field bertipe password di sidebar. Key hanya disimpan di memori sesi Streamlit, digunakan untuk membuat client Fal.ai khusus sesi tersebut, dan tidak ditulis ke file atau environment variable global.

## Install dan Run

```bash
pip install -r requirements.txt
streamlit run app.py --server.port=8502 --server.maxUploadSize=32
```

## Docker

```bash
docker build -t fal-image-tools .
docker run --rm -p 8502:8502 fal-image-tools
```

## Coolify

Gunakan deployment type `Dockerfile`, port aplikasi `8502`, lalu arahkan domain ke service. Deployment harus dapat membuat koneksi HTTPS keluar ke Fal.ai.
