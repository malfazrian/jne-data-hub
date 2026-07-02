"""
Blueprint: Pickup Uploader
Routes: /pickup-uploader, /pickup-uploader/upload,
        /pickup-uploader/download/<filename>
"""
import csv
import datetime
import os
import time
import uuid
from pathlib import Path

import pandas as pd
import psycopg2
from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename


pickup_uploader_bp = Blueprint("pickup_uploader", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PICKUP_OUTPUT_DIR = BASE_DIR / "output" / "pickup_uploader"
TEMP_UPLOAD_DIR = PICKUP_OUTPUT_DIR / "uploads"
TEMP_RESULT_DIR = PICKUP_OUTPUT_DIR / "results"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
DEFAULT_DB_CHUNK_SIZE = 10000
DEFAULT_CSV_CHUNK_SIZE = 50000
TEMP_FILE_MAX_AGE_SECONDS = 24 * 60 * 60
RESULT_FILE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _detect_csv_separator(file_path):
    try:
        sample = file_path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        return ","


def _allowed_file(filename):
    return Path(filename or "").suffix.lower() in ALLOWED_EXTENSIONS


def _db_config():
    return {
        "host": os.getenv("PICKUP_DB_HOST", "192.168.9.105"),
        "port": int(os.getenv("PICKUP_DB_PORT", "5432")),
        "dbname": os.getenv("PICKUP_DB_NAME", "pickup_db"),
        "user": os.getenv("PICKUP_DB_USER", "postgres"),
        "password": os.getenv("PICKUP_DB_PASSWORD", ""),
    }


def _clean_values(series):
    if series is None:
        return []

    cleaned = (
        series.dropna()
        .astype(str)
        .str.strip()
    )
    cleaned = cleaned[cleaned != ""]
    cleaned = cleaned[cleaned.str.lower() != "nan"]
    return cleaned.tolist()


def _awb_column_name(columns):
    for column in columns:
        if str(column).strip().lower() == "awb":
            return column
    return None


def _csv_reader(file_path, header):
    return pd.read_csv(
        file_path,
        dtype=str,
        sep=_detect_csv_separator(file_path),
        header=header,
        chunksize=DEFAULT_CSV_CHUNK_SIZE,
    )


def _read_csv_header(file_path):
    return pd.read_csv(
        file_path,
        dtype=str,
        sep=_detect_csv_separator(file_path),
        nrows=0,
    )


def _read_csv_awbs(file_path):
    awbs = []
    header_df = _read_csv_header(file_path)
    awb_column = _awb_column_name(header_df.columns)

    if awb_column is not None:
        reader = _csv_reader(file_path, header=0)
        for df in reader:
            if awb_column in df.columns:
                awbs.extend(_clean_values(df[awb_column]))
        return awbs

    reader = _csv_reader(file_path, header=None)
    for df in reader:
        if df.empty and len(df.columns) == 0:
            continue

        awbs.extend(_clean_values(df.iloc[:, 0]))
    return awbs


def _read_excel_awbs(file_path):
    suffix = file_path.suffix.lower()
    if suffix == ".xls":
        try:
            import xlrd  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "File .xls membutuhkan library xlrd. Install xlrd di environment server, "
                "atau simpan file sebagai .xlsx lalu upload ulang."
            ) from exc

    awbs = []
    with pd.ExcelFile(file_path) as excel_file:
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)
            if df.empty and len(df.columns) == 0:
                continue

            awb_column = _awb_column_name(df.columns)
            if awb_column is not None:
                awbs.extend(_clean_values(df[awb_column]))
                continue

            raw_df = pd.read_excel(
                excel_file,
                sheet_name=sheet_name,
                dtype=str,
                header=None,
            )
            if not raw_df.empty and len(raw_df.columns) > 0:
                awbs.extend(_clean_values(raw_df.iloc[:, 0]))
    return awbs


def _extract_awbs(file_path):
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_awbs(file_path)
    if suffix in {".xlsx", ".xls"}:
        return _read_excel_awbs(file_path)
    raise ValueError("Format file tidak didukung.")


def _chunks(values, size):
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def _lookup_pickup_data(unique_awbs):
    rows = []
    columns = []
    matched_awbs = set()

    with psycopg2.connect(**_db_config()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cnote_daily_pickup_cnote_no
                ON pickup.cnote_daily (pickup_cnote_no)
                """
            )
            cur.execute("SELECT * FROM pickup.cnote_daily LIMIT 0")
            columns = [desc[0] for desc in cur.description]

            for awb_chunk in _chunks(unique_awbs, DEFAULT_DB_CHUNK_SIZE):
                cur.execute(
                    """
                    SELECT *
                    FROM pickup.cnote_daily
                    WHERE pickup_cnote_no = ANY(%s)
                    """,
                    (awb_chunk,),
                )
                chunk_rows = cur.fetchall()
                rows.extend(chunk_rows)

                if "pickup_cnote_no" in columns:
                    key_index = columns.index("pickup_cnote_no")
                    matched_awbs.update(
                        str(row[key_index]).strip()
                        for row in chunk_rows
                        if row[key_index] is not None
                    )

    return columns, rows, matched_awbs


def _deduplicate_pickup_rows(columns, rows):
    column_keys = [str(column).strip().lower() for column in columns]
    if "pickup_cnote_no" not in column_keys or "pickup_status" not in column_keys:
        return rows

    awb_index = column_keys.index("pickup_cnote_no")
    status_index = column_keys.index("pickup_status")
    selected_rows = {}

    def row_priority(row):
        status = "" if row[status_index] is None else str(row[status_index]).strip().upper()
        return 0 if status.startswith("F") else 1

    for row in rows:
        awb = "" if row[awb_index] is None else str(row[awb_index]).strip()
        if not awb:
            continue

        current_row = selected_rows.get(awb)
        if current_row is None or row_priority(row) > row_priority(current_row):
            selected_rows[awb] = row

    return list(selected_rows.values())


def _excel_text_value(value):
    if value is None:
        return ""

    text = str(value)
    if text == "":
        return ""
    return text if text.startswith("'") else f"'{text}"


def _write_result_csv(columns, rows):
    TEMP_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"pickup_lookup_result_{timestamp}.csv"
    result_path = TEMP_RESULT_DIR / filename

    excluded_columns = {"no"}
    text_columns = {"pickup_cnote_no", "puorder_phone"}
    output_indexes = [
        idx for idx, column in enumerate(columns)
        if str(column).strip().lower() not in excluded_columns
    ]
    output_columns = [columns[idx] for idx in output_indexes]

    with result_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(output_columns)
        for row in rows:
            output_row = []
            for idx in output_indexes:
                column_key = str(columns[idx]).strip().lower()
                value = row[idx]
                if column_key in text_columns:
                    value = _excel_text_value(value)
                output_row.append(value)
            writer.writerow(output_row)

    return filename, result_path


def _remove_file_with_retry(path, attempts=5, delay_seconds=0.25):
    for attempt in range(attempts):
        try:
            if path.exists():
                path.unlink()
            return True
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds)
    return False


def _cleanup_old_temp_uploads():
    if not TEMP_UPLOAD_DIR.exists():
        return

    cutoff = time.time() - TEMP_FILE_MAX_AGE_SECONDS
    for path in TEMP_UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                _remove_file_with_retry(path, attempts=1)
        except Exception as exc:
            print(f"[pickup-uploader] Failed to clean old temporary upload {path}: {exc}")


def _cleanup_old_results():
    if not TEMP_RESULT_DIR.exists():
        return

    cutoff = time.time() - RESULT_FILE_MAX_AGE_SECONDS
    for path in TEMP_RESULT_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                _remove_file_with_retry(path, attempts=1)
        except Exception as exc:
            print(f"[pickup-uploader] Failed to clean old result file {path}: {exc}")


@pickup_uploader_bp.route("/pickup-uploader", methods=["GET"])
def pickup_uploader():
    return render_template("pickup_uploader.html")


@pickup_uploader_bp.route("/pickup-uploader/upload", methods=["POST"])
def upload_pickup_file():
    _cleanup_old_temp_uploads()
    _cleanup_old_results()

    uploaded_file = request.files.get("pickup_file")
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({"error": "Pilih file CSV atau Excel terlebih dahulu."}), 400

    if not _allowed_file(uploaded_file.filename):
        return jsonify({"error": "Format file tidak didukung. Gunakan .csv, .xlsx, atau .xls."}), 400

    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(uploaded_file.filename)
    upload_path = TEMP_UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"

    try:
        uploaded_file.save(upload_path)
        awbs = _extract_awbs(upload_path)
        total_read = len(awbs)

        seen = set()
        unique_awbs = []
        for awb in awbs:
            if awb not in seen:
                seen.add(awb)
                unique_awbs.append(awb)

        if not unique_awbs:
            return jsonify({"error": "Tidak ada AWB valid yang terbaca dari file."}), 400

        columns, rows, matched_awbs = _lookup_pickup_data(unique_awbs)
        rows = _deduplicate_pickup_rows(columns, rows)
        filename, _ = _write_result_csv(columns, rows)

        return jsonify({
            "summary": {
                "total_awb_read": total_read,
                "total_awb_unique": len(unique_awbs),
                "total_db_match": len(rows),
                "total_not_match": max(len(unique_awbs) - len(matched_awbs), 0),
            },
            "download_url": f"/pickup-uploader/download/{filename}",
            "filename": filename,
        })
    except psycopg2.Error as exc:
        return jsonify({
            "error": "Gagal terhubung atau query ke pickup_db. Periksa konfigurasi database dan coba lagi.",
            "detail": str(exc).splitlines()[0],
        }), 500
    except Exception as exc:
        return jsonify({"error": str(exc) or "Proses upload gagal. Coba ulangi beberapa saat lagi."}), 500
    finally:
        try:
            _remove_file_with_retry(upload_path)
        except Exception as exc:
            print(f"[pickup-uploader] Failed to remove temporary upload {upload_path}: {exc}")


@pickup_uploader_bp.route("/pickup-uploader/download/<filename>", methods=["GET"])
def download_pickup_result(filename):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        return jsonify({"error": "Nama file tidak valid."}), 400

    result_path = TEMP_RESULT_DIR / safe_name
    if not result_path.exists():
        return jsonify({"error": "File hasil tidak ditemukan atau sudah dibersihkan."}), 404

    return send_file(result_path, as_attachment=True, download_name=safe_name)
