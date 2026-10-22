# Fal AI Image Tools

Streamlit UI untuk menghapus background dan upscale gambar melalui Fal.ai. Aplikasi ini sepenuhnya memakai API dan tidak mengunduh atau menjalankan model AI lokal.

## Fitur

- Remove background melalui BRIA RMBG 2.0 (`fal-ai/bria/background/remove`) sebagai default.
- BiRefNet v2 (`fal-ai/birefnet/v2`) tetap tersedia dengan pilihan model, resolusi, dan refine foreground.
- Pixelcut Product Photo (`pixelcut/product-photo`) tersedia untuk cutout produk transparan berukuran 1024x1024.
- Crisp Upscale melalui `fal-ai/recraft/upscale/crisp`.
- Maksimal 10 gambar per batch di kedua tab dan 8 MB per gambar.
- Cache sesi menampung hingga 20 hasil Fal.ai.
- Maksimal 2 request Fal.ai berjalan paralel.
- Retry hanya untuk gambar yang gagal.
- Preview, download individual, dan download seluruh hasil sebagai ZIP.
- Tab **Print Size Export**: upload gambar lalu klik **Generate** untuk sekali Crisp Upscale dan ekspor 4 master JPG per gambar, langsung di root ZIP: ISO A1 (594×841 mm), 2:3 (24×36 inci), 3:4 (24×32 inci), dan 4:5 (24×30 inci).
- Ukuran cetak memakai dimensi piksel 300 DPI dan metadata JPG 300 DPI, kualitas 95. Crop tengah memenuhi bidang cetak, orientasi mengikuti sumber (gambar persegi menjadi portrait), dan transparansi menjadi putih.
- Setiap kelompok hanya memiliki satu master; variasi ukuran kecil tidak diekspor terpisah. Ukuran akhir di-resample dengan Lanczos dari hasil upscale; detail cetak bergantung pada kualitas sumber.
- Hasil upscale disimpan di sesi untuk menghindari panggilan API ulang saat rerun atau retry ekspor. ZIP hanya tersedia untuk batch print aktif. Ekspor ukuran besar memerlukan RAM dan ruang temporary server yang cukup.

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

## Painting Mockup

Tab **Painting Mockup** menempelkan painting ke area bingkai secara lokal, tanpa API key:

1. Upload maksimal 20 **Background images** dan 3 **Painting images** (PNG/JPG/JPEG/WebP, maksimal 8 MB dan 24 megapiksel per gambar).
2. Aplikasi otomatis mendeteksi empat sudut bagian dalam bingkai putih/polos dengan OpenCV lokal, termasuk sebelum painting diunggah. Periksa garis area pada grid preview: **Terdeteksi**, **Siap · koreksi manual**, atau **Perlu koreksi**.
3. Bila perlu, buka **Correct frame corners**, pilih background, lalu geser titik bernomor. **Reset** memungkinkan penandaan ulang: kiri atas → kanan atas → kanan bawah → kiri bawah. **Undo** menghapus titik terakhir. **Detect this frame again** menjalankan deteksi ulang pada background terpilih dan mengganti titik saat ini.
4. Klik **Generate Mockups** untuk membuat semua kombinasi background yang siap × painting; 2 background siap × 3 painting menghasilkan 6 mockup. Background yang belum memiliki area valid dilewati, dengan jumlah yang ditampilkan.
5. Download PNG individual atau seluruh hasil dalam ZIP.

Painting mengikuti perspektif bingkai dan memakai crop tengah agar memenuhi bidang tanpa distorsi rasio. Bagian transparan painting menjadi putih. Resolusi hasil mengikuti background setelah koreksi orientasi EXIF. Pilih area di dalam bingkai agar tepi bingkai tetap terlihat; pencahayaan, pantulan kaca, dan objek penutup tidak direkonstruksi otomatis.

Titik tersimpan per background selama sesi aktif; pergantian painting mempertahankan titik. Satu area bingkai per background. Perubahan input atau titik membatalkan hasil lama; klik Generate lagi untuk membuat hasil terbaru. Deteksi hanya dijalankan sekali untuk setiap konten background selama masih di uploader; koreksi manual tidak ditimpa otomatis. Background yang dihapus dari uploader akan dideteksi ulang jika diunggah kembali. Maksimal 60 hasil diproses berurutan dan disimpan di memori sesi.

Deteksi ditujukan untuk satu bingkai utama dengan bagian dalam putih atau mendekati putih dan tepi yang cukup jelas. Area terang yang sama kuat, jendela, bayangan berat, bingkai berisi artwork, dan tepi yang menyatu dengan dinding dapat memerlukan koreksi manual. Ini deteksi geometris lokal, tanpa API atau model generatif; periksa outline sebelum Generate.
