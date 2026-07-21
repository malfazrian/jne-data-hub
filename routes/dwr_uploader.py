"""DWR uploader parsing, indexed lookup, result generation, and Flask routes."""

from __future__ import annotations

import csv
import datetime
import os
from pathlib import Path
import time
import uuid

import duckdb
import pandas as pd
import pyarrow.parquet as pq
from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename


dwr_uploader_bp = Blueprint("dwr_uploader", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DWR_OUTPUT_DIR = BASE_DIR / "output" / "dwr_uploader"
TEMP_UPLOAD_DIR = DWR_OUTPUT_DIR / "uploads"
TEMP_RESULT_DIR = DWR_OUTPUT_DIR / "results"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
DEFAULT_CSV_CHUNK_SIZE = 50000
TEMP_FILE_MAX_AGE_SECONDS = 24 * 60 * 60
RESULT_FILE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class DwrIndexMissingError(RuntimeError):
    pass


class DwrIndexStaleError(RuntimeError):
    pass


def _normalise_awb(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return text[1:].strip() if text.startswith("'") else text


def _excel_text_value(value) -> str:
    text = _normalise_awb(value)
    return f"'{text}" if text else ""


def _key_column(columns):
    for column in columns:
        if str(column).strip().lower() in {"awb", "connote"}:
            return column
    return None


def _clean_values(series) -> list[str]:
    if series is None:
        return []
    values = series.dropna().astype(str).str.strip()
    values = values[(values != "") & (values.str.lower() != "nan")]
    return values.tolist()


def _detect_csv_separator(path: Path) -> str:
    try:
        sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv_connotes(path: Path) -> list[str]:
    separator = _detect_csv_separator(path)
    header = pd.read_csv(path, dtype=str, sep=separator, nrows=0)
    key = _key_column(header.columns)
    connotes: list[str] = []
    if key is not None:
        reader = pd.read_csv(
            path, dtype=str, sep=separator, chunksize=DEFAULT_CSV_CHUNK_SIZE
        )
        for frame in reader:
            connotes.extend(_clean_values(frame[key]))
    else:
        reader = pd.read_csv(
            path,
            dtype=str,
            sep=separator,
            header=None,
            chunksize=DEFAULT_CSV_CHUNK_SIZE,
        )
        for frame in reader:
            if len(frame.columns):
                connotes.extend(_clean_values(frame.iloc[:, 0]))
    return connotes


def _read_excel_connotes(path: Path) -> list[str]:
    if path.suffix.lower() == ".xls":
        try:
            import xlrd  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "File .xls membutuhkan library xlrd; gunakan .xlsx atau install xlrd."
            ) from exc
    with pd.ExcelFile(path) as workbook:
        multi_sheet = len(workbook.sheet_names) > 1
        connotes: list[str] = []
        for sheet in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet, dtype=str)
            key = _key_column(frame.columns)
            if key is not None:
                connotes.extend(_clean_values(frame[key]))
            elif not multi_sheet:
                raw = pd.read_excel(workbook, sheet_name=sheet, dtype=str, header=None)
                if len(raw.columns):
                    connotes.extend(_clean_values(raw.iloc[:, 0]))
        if multi_sheet and not connotes:
            raise ValueError("Workbook multi-sheet wajib memiliki kolom AWB atau CONNOTE.")
        return connotes


def _extract_connotes(path: str | Path) -> list[str]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_connotes(path)
    if suffix in {".xlsx", ".xls"}:
        return _read_excel_connotes(path)
    raise ValueError("Format file tidak didukung.")


def _parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("tahun=*/bulan=*/data.parquet") if path.is_file())


def _validate_index(root: Path, index: Path) -> list[Path]:
    if not root.is_dir():
        raise DwrIndexMissingError("Database DWR belum tersedia.")
    if not index.is_file():
        raise DwrIndexMissingError("Index AWB DWR belum tersedia.")
    con = duckdb.connect(str(index), read_only=True)
    try:
        rows = con.execute(
            "SELECT relative_path, file_size, modified_ns, schema_version FROM dwr_file_manifest"
        ).fetchall()
    except duckdb.Error as exc:
        raise DwrIndexMissingError("Schema index AWB DWR tidak valid.") from exc
    finally:
        con.close()
    indexed = {row[0]: (row[1], row[2]) for row in rows}
    files = _parquet_files(root)
    current = {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in files
    }
    if any(row[3] != 1 for row in rows) or indexed != current:
        raise DwrIndexStaleError("Index AWB DWR stale; jalankan dwr_awb_index.py --sync.")
    return files


def _normalized_sql(column: str) -> str:
    return (
        f"CASE WHEN left(trim({column}), 1) = chr(39) "
        f"THEN trim(substr(trim({column}), 2)) ELSE trim({column}) END"
    )


def _lookup_dwr_data(
    connotes: list[str], parquet_root: str | Path, index_path: str | Path
) -> tuple[list[str], list[tuple], set[int]]:
    root = Path(parquet_root).resolve()
    index = Path(index_path)
    all_files = _validate_index(root, index)
    if not all_files:
        raise DwrIndexMissingError("Database DWR belum berisi file Parquet.")
    dwr_columns = [name for name in pq.ParquetFile(all_files[0]).schema_arrow.names if name != "AWB"]
    keys = sorted({_normalise_awb(value) for value in connotes if _normalise_awb(value)})
    locator = duckdb.connect(str(index), read_only=True)
    try:
        locator.execute("CREATE TEMP TABLE requested(join_awb VARCHAR PRIMARY KEY)")
        if keys:
            locator.executemany("INSERT INTO requested VALUES (?)", [(key,) for key in keys])
        located = locator.execute(
            f"""
            SELECT r.join_awb, l.awb, m.relative_path
            FROM requested r
            JOIN dwr_awb_locator l ON r.join_awb = {_normalized_sql('l.awb')}
            JOIN dwr_file_manifest m ON m.file_id = l.file_id
            """
        ).fetchall()
    finally:
        locator.close()
    selected_paths = sorted({str((root / Path(row[2])).resolve()) for row in located})
    records: dict[str, dict] = {}
    if selected_paths:
        query = duckdb.connect()
        try:
            frame = query.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true)", [selected_paths]
            ).df()
        finally:
            query.close()
        frame["_join_awb"] = frame["AWB"].map(_normalise_awb)
        duplicates = frame["_join_awb"].duplicated(keep=False)
        if duplicates.any():
            raise ValueError(f"Duplicate DWR AWB ditemukan: {frame.loc[duplicates, '_join_awb'].iloc[0]}")
        records = {row["_join_awb"]: row for _, row in frame.iterrows()}

    rows: list[tuple] = []
    matched: set[int] = set()
    for ordinal, source in enumerate(connotes):
        record = records.get(_normalise_awb(source))
        values = []
        if record is not None:
            matched.add(ordinal)
            for column in dwr_columns:
                value = record.get(column)
                values.append(None if value is None or pd.isna(value) else value)
        else:
            values = [None] * len(dwr_columns)
        rows.append((_excel_text_value(source), *values))
    return ["CONNOTE", *dwr_columns], rows, matched


def _format_csv_value(value):
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime.datetime)):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%d/%m/%Y")
    return value


def _write_result_csv(columns: list[str], rows: list[tuple]) -> tuple[str, Path]:
    TEMP_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"dwr_lookup_result_{stamp}_{uuid.uuid4().hex[:8]}.csv"
    path = TEMP_RESULT_DIR / filename
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows([[_format_csv_value(value) for value in row] for row in rows])
    return filename, path


def _remove_file_with_retry(path: Path, attempts=5, delay_seconds=0.25) -> None:
    for attempt in range(attempts):
        try:
            if path.exists():
                path.unlink()
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds)


def _cleanup_old_files(directory: Path, max_age_seconds: int) -> None:
    if not directory.exists():
        return
    cutoff = time.time() - max_age_seconds
    for path in directory.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            try:
                _remove_file_with_retry(path, attempts=1)
            except Exception as exc:
                print(f"[dwr-uploader] cleanup failed for {path.name}: {exc}")


@dwr_uploader_bp.route("/dwr-uploader", methods=["GET"])
def dwr_uploader():
    return render_template("dwr_uploader.html")


@dwr_uploader_bp.route("/dwr-uploader/upload", methods=["POST"])
def upload_dwr_file():
    _cleanup_old_files(TEMP_UPLOAD_DIR, TEMP_FILE_MAX_AGE_SECONDS)
    _cleanup_old_files(TEMP_RESULT_DIR, RESULT_FILE_MAX_AGE_SECONDS)
    uploaded = request.files.get("dwr_file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Pilih file CSV atau Excel terlebih dahulu."}), 400
    if Path(uploaded.filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Format tidak didukung. Gunakan CSV, XLSX, atau XLS."}), 400

    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = TEMP_UPLOAD_DIR / f"{uuid.uuid4().hex}_{secure_filename(uploaded.filename)}"
    try:
        uploaded.save(upload_path)
        connotes = _extract_connotes(upload_path)
        if not connotes:
            return jsonify({"error": "Tidak ada AWB atau connote valid yang terbaca."}), 400
        parquet_root = Path(os.getenv("DWR_PARQUET_DIR", "./dwr_parquet"))
        index_path = Path(os.getenv("DWR_AWB_INDEX_PATH", "awb_dwr_index.duckdb"))
        columns, rows, matched = _lookup_dwr_data(connotes, parquet_root, index_path)
        filename, _ = _write_result_csv(columns, rows)
        unique_count = len({_normalise_awb(value) for value in connotes if _normalise_awb(value)})
        return jsonify(
            {
                "summary": {
                    "total_connote_read": len(connotes),
                    "total_connote_unique": unique_count,
                    "total_db_match": len(matched),
                    "total_not_match": len(connotes) - len(matched),
                },
                "download_url": f"/dwr-uploader/download/{filename}",
                "filename": filename,
            }
        )
    except (DwrIndexMissingError, DwrIndexStaleError) as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[dwr-uploader] query failed: {type(exc).__name__}: {exc}")
        return jsonify({"error": "Query DWR gagal. Hubungi administrator aplikasi."}), 500
    finally:
        try:
            _remove_file_with_retry(upload_path)
        except Exception as exc:
            print(f"[dwr-uploader] upload cleanup failed: {exc}")


@dwr_uploader_bp.route("/dwr-uploader/download/<path:filename>", methods=["GET"])
def download_dwr_result(filename):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        return jsonify({"error": "Nama file tidak valid."}), 400
    result_path = TEMP_RESULT_DIR / safe_name
    if not result_path.is_file():
        return jsonify({"error": "File hasil tidak ditemukan atau sudah dibersihkan."}), 404
    return send_file(result_path, as_attachment=True, download_name=safe_name)
