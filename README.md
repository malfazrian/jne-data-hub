# Performance App

Aplikasi web internal berbasis Flask untuk:
- Generate data performance project (mode Performance, Full, dan Report) dari sumber CSV/Parquet.
- Request data APEX by customer ID (otomasi Selenium).
- Upload & proses file APEX (otomasi query/update AWB).
- Menjelajah dan mengunduh report harian beserta fitur favorit/recent download per user.

Aplikasi dijalankan dengan Waitress dan menyimpan output proses di folder `output/`.

## Fitur Utama

### 1. Performance Processor
- Mode `normal` (`/`) untuk output performance standar.
- Mode `full` (`/full`) untuk ekstrak data lebih lengkap + filter kategori.
- Mode `report` (`/report`) untuk kebutuhan report berbasis kriteria.
- Mendukung sumber data:
  - `csv` (network share CSV)
  - `parquet` (DuckDB query langsung ke file parquet)
- Progress proses berjalan async (request ID + endpoint progress).

### 2. Report Explorer
- Endpoint UI: `/report_explorer`
- Menampilkan list file report berdasarkan tanggal (`/list_reports`).
- Download single/multiple file (otomatis zip jika lebih dari 1 file).
- Menyimpan preferensi user per-IP:
  - favorites
  - recent downloads

### 3. APEX Uploader
- Endpoint UI: `/apex_uploader`
- Upload source file, jalankan job APEX, pantau progress via SSE.
- Hasil dapat diunduh dari endpoint download khusus job.

### 4. APEX Requester
- Endpoint UI: `/apex_requester`
- Request data APEX berdasarkan customer ID + rentang tanggal.
- Mendukung fallback multi-host APEX.
- Progress per task dan hasil merged CSV dapat diunduh.

### 5. Admin Status Endpoints
- `/parquet_source_status`: status file source parquet 3 bulan terakhir.
- `/report_history_status`: ringkasan status update query/edit report.
- Akses dibatasi untuk request dari host mesin server.

## Struktur Folder

```text
performance_app/
  app.pyw
  run main.bat
  data/
    project_reference.csv
    user_preferences.json
  output/
    master_data/
    requester/
    uploader/
  scripts/
    query_performance_custom.py
    query_performance_parquet.py
    apex_query.py
    apex_query_by_id.py
    ...
  static/
  templates/
```

## Kebutuhan Sistem

- Windows (sesuai environment proyek saat ini)
- Python 3.11+ (disarankan 3.13 seperti konfigurasi `run main.bat`)
- Google Chrome (untuk modul Selenium)
- ChromeDriver sesuai versi Chrome (pastikan ada di PATH atau terpasang di sistem)
- Akses ke network share internal yang dipakai aplikasi

## Dependensi Python

Install minimal package berikut:

```bash
pip install flask waitress pandas numpy openpyxl python-dateutil duckdb selenium pywin32
```

Catatan:
- `pywin32` dipakai untuk modul COM (`win32com`) pada proses file tertentu.
- Jika proses parquet membutuhkan backend tambahan di environment Anda, tambahkan `pyarrow`.

## Menjalankan Aplikasi

Sebelum menjalankan aplikasi, siapkan env lokal:

```bash
copy .env.example .env
```

Lalu isi `.env` sesuai server/path internal Anda.

### Opsi 1 (paling sederhana)
Gunakan script bawaan:

```bat
run main.bat
```

Script ini menjalankan:

```bat
pythonw.exe app.pyw
```

### Opsi 2 (manual)

```bash
python app.pyw
```

Default server:
- Host: `0.0.0.0`
- Port: `5000`

Akses dari browser:
- `http://localhost:5000`

## Endpoint Utama

### Halaman
- `GET /` -> Performance mode
- `GET /full` -> Full mode
- `GET /report` -> Report mode
- `GET /apex_uploader` -> UI uploader APEX
- `GET /apex_requester` -> UI requester APEX
- `GET /report_explorer` -> UI penjelajah report

### Proses Performance
- `POST /run`
- `POST /run_full`
- `POST /run_report`
- `GET /progress/<request_id>` (SSE)
- `GET /progress_status/<request_id>`
- `GET /download/<request_id>`

### Report Explorer
- `GET /list_reports?date=YYYY-MM-DD`
- `GET /download_reports?date=YYYY-MM-DD&files=<path_rel>`
- `GET /user_preferences`
- `POST /toggle_favorite`
- `POST /track_download`

### APEX Uploader
- `POST /start_job`
- `GET /progress-apex/<job_id>` (SSE)
- `GET /check-job/<job_id>`
- `POST /cancel-job/<job_id>`
- `GET /download-apex/<job_id>`

### APEX Requester
- `POST /start-request`
- `GET /progress-request/<task_id>`
- `POST /cancel-request/<task_id>`
- `GET /download-request/<task_id>`

## Konfigurasi Penting

Konfigurasi sensitif dan environment-specific sudah dipindahkan ke `.env`.

Variable yang tersedia (lihat `.env.example`):
- `FLASK_SECRET_KEY`
- `APP_HOST`
- `APP_PORT`
- `APP_THREADS`
- `CSV_BASE_PATH`
- `PARQUET_BASE_PATH`
- `PARQUET_SOURCE_BASE`
- `PROCESS_HISTORY_SOURCE`
- `PARQUET_PROCESSED_FILE`
- `REPORT_EXPLORER_BASE`
- `APEX_DEFAULT_HOSTS`

Catatan:
- `.env` sudah di-ignore oleh git lewat `.gitignore`.
- Simpan nilai asli hanya di `.env` lokal, jangan di file source code.

## Catatan Operasional

- Folder output lama dibersihkan otomatis (berdasarkan umur file/folder).
- Progress dan status job disimpan in-memory selama proses berjalan.
- Preferensi user disimpan di `data/user_preferences.json` per alamat IP.
- Untuk deployment production, pindahkan `secret_key` Flask ke environment variable.

## Troubleshooting Singkat

1. Tidak bisa akses data
- Cek koneksi ke network share dan hak akses folder.

2. Job Selenium gagal
- Pastikan Chrome + ChromeDriver compatible.
- Pastikan host APEX yang dipilih aktif.

3. Progress selesai tapi file tidak ada
- Cek log error di console server.
- Pastikan folder output tidak terhapus oleh cleanup process lain.
