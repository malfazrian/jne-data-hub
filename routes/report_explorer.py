"""
Blueprint: Report Explorer
Routes: /report_explorer, /parquet_source_status, /report_history_status,
        /daily_load_awb_count_status, /list_reports, /user_preferences, /toggle_favorite,
        /track_download, /download_reports, /backup_status
"""
import os
import csv
import json
import datetime
import io
import threading
import time
import zipfile
from pathlib import Path

from flask import (
    Blueprint, render_template, request,
    send_file, jsonify, current_app,
)

from scripts import backup_syncer
from routes.shared import (
    BASE_DIR,
    PROCESS_HISTORY_SOURCE, PARQUET_PROCESSED_FILE,
    PARQUET_SOURCE_BASE, REPORT_EXPLORER_BASE,
    DAILY_LOAD_AWB_CHECK_DIR,
    _BULAN, _prefs_lock, _load_prefs_internal, _save_prefs_internal,
    get_client_ip,
    extract_file_key, cleanup_old_recent_downloads,
)

report_explorer_bp = Blueprint("report_explorer", __name__)

REPORT_EXPLORER_CACHE_TTL_SECONDS = int(os.getenv("REPORT_EXPLORER_CACHE_TTL_SECONDS", "60"))
STATUS_CACHE_TTL_SECONDS = int(os.getenv("REPORT_EXPLORER_STATUS_CACHE_TTL_SECONDS", "300"))
REPORT_VIEWER_NOTICE_FILE = os.getenv(
    "REPORT_VIEWER_NOTICE_FILE",
    os.path.join(BASE_DIR, "data", "report_viewer_notice.json"),
)
MANUALS_PATH = os.getenv(
    "MANUALS_PATH",
    r"D:\RYAN\Python Scripts\Bot Report Gabungan\data\manuals.csv",
)
MANUAL_COLUMNS = [
    "AWB", "ID_ACCOUNT", "TGL_ENTRY", "STATUS_POD", "RECEIVED/REASON",
    "URL_TTD", "URL_FOTO", "NOREF",
]
MANUAL_OVERRIDE_TARGET_COLUMNS = [
    "NOREF", "STATUS_POD", "RECEIVED/REASON", "URL_TTD", "URL_FOTO",
]

REPORT_VIEWER_NOTICE_ICONS = {
    "primary": "bi-info-circle-fill",
    "secondary": "bi-info-circle-fill",
    "success": "bi-check-circle-fill",
    "danger": "bi-exclamation-octagon-fill",
    "warning": "bi-exclamation-triangle-fill",
    "info": "bi-info-circle-fill",
}

_cache_lock = threading.Lock()
_cache_store = {}
_manuals_lock = threading.Lock()


def _normalize_manual_awb(value):
    return str(value or "").strip().lstrip("'")


def _read_manual_rows():
    if not os.path.exists(MANUALS_PATH):
        return []
    with open(MANUALS_PATH, "r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or "AWB" not in reader.fieldnames:
            raise ValueError("manuals.csv tidak memiliki kolom AWB")
        return [
            {column: str(row.get(column, "") or "") for column in MANUAL_COLUMNS}
            for row in reader
        ]


def _write_manual_rows(rows):
    target = Path(MANUALS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=MANUAL_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            csv_file.flush()
            os.fsync(csv_file.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _cache_get(key, ttl_seconds):
    cached = _cache_store.get(key)
    if not cached:
        return None
    cached_at, data = cached
    if time.time() - cached_at > ttl_seconds:
        _cache_store.pop(key, None)
        return None
    return data


def _cached_data(key, ttl_seconds, loader):
    with _cache_lock:
        cached = _cache_get(key, ttl_seconds)
        if cached is not None:
            return cached

    data = loader()

    with _cache_lock:
        _cache_store[key] = (time.time(), data)
    return data


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_report_viewer_notice(raw_notice):
    if not isinstance(raw_notice, dict):
        return None
    if not raw_notice.get("enabled", True):
        return None

    message = str(raw_notice.get("message", "")).strip()
    if not message:
        return None

    category = str(raw_notice.get("category", "info")).strip().lower()
    if category not in REPORT_VIEWER_NOTICE_ICONS:
        category = "info"

    title = str(raw_notice.get("title", "")).strip()
    icon = str(raw_notice.get("icon", "")).strip() or REPORT_VIEWER_NOTICE_ICONS[category]

    return {
        "category": category,
        "icon": icon,
        "title": title,
        "message": message,
        "dismissible": bool(raw_notice.get("dismissible", False)),
    }


def load_report_viewer_notice():
    if os.path.exists(REPORT_VIEWER_NOTICE_FILE):
        try:
            with open(REPORT_VIEWER_NOTICE_FILE, "r", encoding="utf-8") as file:
                return _normalize_report_viewer_notice(json.load(file))
        except Exception as exc:
            current_app.logger.warning(f"Error reading report viewer notice config: {exc}")
            return None

    env_message = os.getenv("REPORT_VIEWER_NOTICE_MESSAGE", "").strip()
    if not env_message:
        return None

    return _normalize_report_viewer_notice({
        "enabled": _env_bool("REPORT_VIEWER_NOTICE_ENABLED", True),
        "category": os.getenv("REPORT_VIEWER_NOTICE_CATEGORY", "info"),
        "icon": os.getenv("REPORT_VIEWER_NOTICE_ICON", ""),
        "title": os.getenv("REPORT_VIEWER_NOTICE_TITLE", ""),
        "message": env_message,
        "dismissible": _env_bool("REPORT_VIEWER_NOTICE_DISMISSIBLE", False),
    })


# ── History / status helpers ──────────────────────────────────────────────────

def parse_history_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_history_status(date_value, today):
    if not date_value:
        return {"icon": "💤", "label": "Tidak ada data", "date": None}

    age_days = (today - date_value).days
    if age_days <= 0:
        icon  = "✅"
        label = "Update hari ini"
    elif age_days <= 7:
        icon  = "❌"
        label = f"Belum update {age_days} hari"
    else:
        icon  = "💤"
        label = f"Belum update {age_days} hari"

    return {"icon": icon, "label": label, "date": date_value.isoformat()}


def normalize_history_name(value):
    normalized = (value or "").strip().lower()
    return " ".join(normalized.split())


def build_query_aliases(report_name):
    aliases = {normalize_history_name(report_name)}
    if "_" in report_name:
        aliases.add(normalize_history_name(report_name.replace("_", " - ")))
        aliases.add(normalize_history_name(report_name.replace("_", " ")))
    return {alias for alias in aliases if alias}


def find_best_query_match(edit_report_name, query_dates):
    edit_normalized = normalize_history_name(edit_report_name)
    best_match = None

    for query_name, query_date in query_dates.items():
        for alias in build_query_aliases(query_name):
            is_match = (
                edit_normalized == alias
                or edit_normalized.startswith(f"{alias} - ")
            )
            if not is_match:
                continue
            candidate = (len(alias), query_name, query_date)
            if best_match is None or candidate[0] > best_match[0]:
                best_match = candidate

    return best_match[2] if best_match else None


def load_parquet_source_status():
    """Scan source Excel dirs and show what the parquet stage would process next.

    The parquet script reprocesses all OPEN/LOAD files in a month when any
    OPEN/LOAD source is new, failed, or has a different mtime. Mirror that here
    so the viewer does not show a month as safe while the next run still has work.
    """
    today = datetime.date.today()

    months_to_check = []
    year, month = today.year, today.month
    for _ in range(3):
        months_to_check.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    processed_log = {}
    log_exists = os.path.exists(PARQUET_PROCESSED_FILE)
    if log_exists:
        try:
            with open(PARQUET_PROCESSED_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for k, v in raw.items():
                processed_log[os.path.normcase(k)] = v
        except Exception as exc:
            current_app.logger.error(f"Error reading parquet processed log: {exc}")

    month_rows = []
    grand_total = grand_done = grand_failed = grand_unprocessed = 0
    grand_changed = grand_needs_run = 0

    def file_key(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def classify_file(path):
        rec = processed_log.get(os.path.normcase(path))
        key = file_key(path)

        if rec is None:
            return "unprocessed"

        if isinstance(rec, dict):
            status = rec.get("status")
            previous_key = rec.get("mtime")
            if status == "failed":
                return "failed"
            if status == "done" and previous_key == key:
                return "done"
            return "changed"

        if isinstance(rec, (int, float)):
            return "done" if rec == key else "changed"

        return "unprocessed"

    for yr, mo in months_to_check:
        label    = f"{_BULAN[mo]} {yr}"
        dir_name = f"{mo}. {_BULAN[mo]} {yr}"
        dir_path = os.path.join(PARQUET_SOURCE_BASE, str(yr), dir_name)
        dir_exists = os.path.isdir(dir_path)

        mo_total = mo_done = mo_failed = mo_unprocessed = 0
        mo_changed = mo_needs_run = 0

        if dir_exists:
            try:
                excel_files = []
                for entry in os.scandir(dir_path):
                    if not entry.is_file():
                        continue
                    if entry.name.startswith("~$"):
                        continue
                    if not entry.name.lower().endswith(".xlsx"):
                        continue
                    if not any(keyword in entry.name.upper() for keyword in ("OPEN", "LOAD", "CLOSE")):
                        continue
                    excel_files.append(entry.path)

                file_rows = []
                open_has_work = False
                for path in excel_files:
                    name = os.path.basename(path).upper()
                    is_open = ("OPEN" in name) or ("LOAD" in name)
                    status = classify_file(path)
                    if is_open and status != "done":
                        open_has_work = True
                    file_rows.append((path, is_open, status))

                for _path, is_open, status in file_rows:
                    mo_total += 1

                    script_will_process = status != "done" or (is_open and open_has_work)
                    if script_will_process:
                        mo_needs_run += 1
                    else:
                        mo_done += 1

                    if status == "failed":
                        mo_failed += 1
                    elif status == "unprocessed":
                        mo_unprocessed += 1
                    elif status == "changed":
                        mo_changed += 1
            except Exception as exc:
                current_app.logger.error(f"Error scanning {dir_path}: {exc}")

        grand_total       += mo_total
        grand_done        += mo_done
        grand_failed      += mo_failed
        grand_unprocessed += mo_unprocessed
        grand_changed     += mo_changed
        grand_needs_run   += mo_needs_run

        month_rows.append({
            "label":      label,
            "dir_exists": dir_exists,
            "total":      mo_total,
            "done":       mo_done,
            "failed":     mo_failed,
            "unprocessed": mo_unprocessed,
            "changed":     mo_changed,
            "needs_run":   mo_needs_run,
        })

    return {
        "months":           month_rows,
        "total_excel":      grand_total,
        "total_done":       grand_done,
        "total_failed":     grand_failed,
        "total_unprocessed": grand_unprocessed,
        "total_changed":     grand_changed,
        "total_needs_run":   grand_needs_run,
        "log_source":       PARQUET_PROCESSED_FILE,
        "log_exists":       log_exists,
    }


def load_report_history_rows():
    """Build report history rows from edit entries and inherit query status."""
    if not os.path.exists(PROCESS_HISTORY_SOURCE):
        raise FileNotFoundError(PROCESS_HISTORY_SOURCE)

    with open(PROCESS_HISTORY_SOURCE, "r", encoding="utf-8") as file:
        history = json.load(file)

    query_dates = {}
    edit_dates  = {}

    for key, value in history.items():
        if key.startswith("query_"):
            report_name = key[len("query_"):].strip()
        elif key.startswith("edit_"):
            report_name = os.path.splitext(key[len("edit_"):].strip())[0].strip()
        else:
            continue

        if not report_name:
            continue

        parsed_date = parse_history_date(value)
        if key.startswith("query_"):
            existing = query_dates.get(report_name)
            if existing is None or (parsed_date and parsed_date > existing):
                query_dates[report_name] = parsed_date
        else:
            existing = edit_dates.get(report_name)
            if existing is None or (parsed_date and parsed_date > existing):
                edit_dates[report_name] = parsed_date

    today  = datetime.date.today()
    result = []
    for report_name in sorted(edit_dates):
        edit_date  = edit_dates[report_name]
        query_date = find_best_query_match(report_name, query_dates)
        result.append({
            "report_name": report_name,
            "query":       build_history_status(query_date, today),
            "edit":        build_history_status(edit_date, today),
        })

    return result


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_iso_datetime(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _build_daily_load_status(raw):
    success = bool(raw.get("success"))
    is_match = bool(raw.get("is_match"))
    difference = raw.get("difference_web_minus_output")

    try:
        diff_num = int(difference)
    except (TypeError, ValueError):
        diff_num = None

    if success and is_match:
        return "Pas", "success"
    if diff_num is None:
        return "Tidak diketahui", "secondary"
    if diff_num > 0:
        return "Load kurang", "danger"
    if diff_num < 0:
        return "Load lebih", "warning"
    return "Tidak match", "danger"


def load_daily_load_awb_count_rows(days=7):
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=days - 1)
    source_dir = DAILY_LOAD_AWB_CHECK_DIR

    latest_by_target_date = {}
    if os.path.isdir(source_dir):
        for path in Path(source_dir).glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as file:
                    raw = json.load(file)
            except Exception as exc:
                current_app.logger.warning(f"Error reading AWB count check {path}: {exc}")
                continue

            target_date = _parse_iso_date(raw.get("target_date"))
            if not target_date or target_date < start_date or target_date > today:
                continue

            checked_at = _parse_iso_datetime(raw.get("checked_at"))
            current = latest_by_target_date.get(target_date)
            if current is None or (checked_at or datetime.datetime.min) > current["checked_at_sort"]:
                status_label, status_class = _build_daily_load_status(raw)
                latest_by_target_date[target_date] = {
                    "target_date": target_date.isoformat(),
                    "checked_at": raw.get("checked_at"),
                    "status": raw.get("status"),
                    "status_label": status_label,
                    "status_class": status_class,
                    "is_match": bool(raw.get("is_match")),
                    "output_awb_count": raw.get("output_awb_count"),
                    "web_awb_count": raw.get("web_awb_count"),
                    "difference_web_minus_output": raw.get("difference_web_minus_output"),
                    "email_subject": raw.get("email_subject"),
                    "output_file": raw.get("output_file"),
                    "source_file": str(path),
                    "checked_at_sort": checked_at or datetime.datetime.min,
                }

    rows = []
    for target_date in sorted(latest_by_target_date, reverse=True):
        row = latest_by_target_date[target_date].copy()
        row.pop("checked_at_sort", None)
        rows.append(row)

    return rows, source_dir, start_date.isoformat(), today.isoformat()


def _find_project_pic_file():
    data_dir = os.path.join(BASE_DIR, "data")
    candidates = [
        os.path.join(data_dir, "PROJECT PIC.xlsx"),
        os.path.join(data_dir, "project_PIC.xlsx"),
        os.path.join(data_dir, "project_pic.xlsx"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path

    if os.path.isdir(data_dir):
        for name in os.listdir(data_dir):
            if name.startswith("~$"):
                continue
            if name.lower() == "project pic.xlsx" or name.lower() == "project_pic.xlsx":
                return os.path.join(data_dir, name)
    return candidates[0]


def load_project_pic_rows():
    source_path = _find_project_pic_file()
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)

    import pandas as pd

    required_columns = ["BIG_GROUPING_CUST", "PIC SUPPORT DATA", "PIC BACK UP"]
    workbook = pd.ExcelFile(source_path)
    selected_df = None

    for sheet_name in workbook.sheet_names:
        df = pd.read_excel(source_path, sheet_name=sheet_name)
        df.columns = [str(col).strip() for col in df.columns]
        if all(col in df.columns for col in required_columns):
            selected_df = df
            break

    if selected_df is None:
        raise ValueError("Kolom PROJECT PIC tidak ditemukan di workbook.")

    selected_df = selected_df[required_columns].fillna("")
    rows = []
    for row in selected_df.to_dict("records"):
        project = str(row["BIG_GROUPING_CUST"]).strip()
        pic_support = str(row["PIC SUPPORT DATA"]).strip()
        pic_backup = str(row["PIC BACK UP"]).strip()
        if not (project or pic_support or pic_backup):
            continue
        rows.append({
            "project": project,
            "pic_data_support": pic_support,
            "pic_back_up": pic_backup,
        })

    return rows, source_path


# ── Helper: build report path ─────────────────────────────────────────────────

def _build_report_path(date_obj):
    bulan_nama  = _BULAN[date_obj.month]
    tahun_2digit = str(date_obj.year)[-2:]
    bulan_num   = f"{date_obj.month:02d}"
    tanggal_num = f"{date_obj.day:02d}"
    return os.path.join(
        REPORT_EXPLORER_BASE,
        "ALL REPORT GABUNGAN",
        f"{bulan_num}. {bulan_nama} {tahun_2digit}",
        f"{tanggal_num} {bulan_num} {tahun_2digit}",
        "trial BOT",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@report_explorer_bp.route("/report_explorer", methods=["GET"])
def report_explorer():
    user_ip = get_client_ip()
    cleanup_old_recent_downloads(user_ip)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return render_template(
        "report_viewer.html",
        today=today,
        report_viewer_notice=load_report_viewer_notice(),
    )


@report_explorer_bp.route("/manual_overrides", methods=["GET"])
def list_manual_overrides():
    query = _normalize_manual_awb(request.args.get("q", "")).lower()
    try:
        with _manuals_lock:
            rows = _read_manual_rows()
        if query:
            rows = [
                row for row in rows
                if query in _normalize_manual_awb(row.get("AWB")).lower()
                or query in str(row.get("NOREF", "")).lower()
                or query in str(row.get("RECEIVED/REASON", "")).lower()
            ]
        rows.reverse()
        return jsonify({"rows": rows[:50], "total": len(rows)})
    except Exception as exc:
        current_app.logger.exception("Gagal membaca manual overrides")
        return jsonify({"error": f"Gagal membaca manuals.csv: {exc}"}), 500


@report_explorer_bp.route("/manual_overrides", methods=["POST"])
def save_manual_override():
    payload = request.get_json(silent=True) or {}
    awb = _normalize_manual_awb(payload.get("AWB"))
    original_awb = _normalize_manual_awb(payload.get("original_awb")) or awb
    if not awb:
        return jsonify({"error": "AWB wajib diisi."}), 400
    if len(awb) > 100:
        return jsonify({"error": "AWB maksimal 100 karakter."}), 400

    saved_row = {
        column: str(payload.get(column, "") or "").strip()
        for column in MANUAL_COLUMNS
    }
    # Excel-friendly text representation, consistent with the existing CSV.
    saved_row["AWB"] = f"'{awb}"

    try:
        with _manuals_lock:
            rows = _read_manual_rows()
            matching_indexes = [
                index for index, row in enumerate(rows)
                if _normalize_manual_awb(row.get("AWB")) == original_awb
            ]
            if matching_indexes:
                first_index = matching_indexes[0]
                rows[first_index] = saved_row
                duplicate_indexes = set(matching_indexes[1:])
                rows = [row for index, row in enumerate(rows) if index not in duplicate_indexes]
                action = "updated"
            else:
                rows.append(saved_row)
                action = "created"
            _write_manual_rows(rows)
        return jsonify({"message": "Manual override berhasil disimpan.", "action": action, "row": saved_row})
    except Exception as exc:
        current_app.logger.exception("Gagal menyimpan manual override")
        return jsonify({"error": f"Gagal menyimpan manuals.csv: {exc}"}), 500


@report_explorer_bp.route("/manual_overrides/bulk", methods=["POST"])
def save_manual_overrides_bulk():
    payload = request.get_json(silent=True) or {}
    target_column = str(payload.get("target_column", "NOREF")).strip().upper()
    if target_column not in MANUAL_OVERRIDE_TARGET_COLUMNS:
        return jsonify({"error": "Kolom tujuan tidak valid."}), 400

    incoming_rows = payload.get("rows")
    if not isinstance(incoming_rows, list) or not incoming_rows:
        return jsonify({"error": "Belum ada data valid untuk disimpan."}), 400
    if len(incoming_rows) > 5000:
        return jsonify({"error": "Maksimal 5.000 baris per sekali simpan."}), 400

    # Last pasted value wins when an AWB occurs more than once.
    values_by_awb = {}
    invalid_count = 0
    for item in incoming_rows:
        if not isinstance(item, dict):
            invalid_count += 1
            continue
        awb = _normalize_manual_awb(item.get("awb"))
        value = str(item.get("value", "") or "").strip()
        if not awb or len(awb) > 100 or not value:
            invalid_count += 1
            continue
        values_by_awb[awb] = value

    if not values_by_awb:
        return jsonify({"error": "Tidak ada pasangan AWB dan nilai yang valid."}), 400

    try:
        with _manuals_lock:
            rows = _read_manual_rows()
            first_index_by_awb = {}
            for index, row in enumerate(rows):
                normalized = _normalize_manual_awb(row.get("AWB"))
                first_index_by_awb.setdefault(normalized, index)

            created = 0
            updated = 0
            for awb, value in values_by_awb.items():
                if awb in first_index_by_awb:
                    row = rows[first_index_by_awb[awb]]
                    row[target_column] = value
                    row["AWB"] = f"'{awb}"
                    updated += 1
                else:
                    new_row = {column: "" for column in MANUAL_COLUMNS}
                    new_row["AWB"] = f"'{awb}"
                    new_row[target_column] = value
                    first_index_by_awb[awb] = len(rows)
                    rows.append(new_row)
                    created += 1

            # Consolidate pre-existing duplicate rows for AWBs touched by this batch.
            touched = set(values_by_awb)
            seen_touched = set()
            deduped_rows = []
            removed_duplicates = 0
            for row in rows:
                normalized = _normalize_manual_awb(row.get("AWB"))
                if normalized in touched:
                    if normalized in seen_touched:
                        removed_duplicates += 1
                        continue
                    seen_touched.add(normalized)
                deduped_rows.append(row)

            _write_manual_rows(deduped_rows)

        return jsonify({
            "message": f"{len(values_by_awb)} manual override berhasil disimpan.",
            "processed": len(values_by_awb),
            "created": created,
            "updated": updated,
            "invalid": invalid_count,
            "input_duplicates": len(incoming_rows) - invalid_count - len(values_by_awb),
            "removed_duplicates": removed_duplicates,
            "target_column": target_column,
        })
    except Exception as exc:
        current_app.logger.exception("Gagal menyimpan bulk manual overrides")
        return jsonify({"error": f"Gagal menyimpan manuals.csv: {exc}"}), 500


@report_explorer_bp.route("/manual_overrides/<path:awb>", methods=["DELETE"])
def delete_manual_override(awb):
    normalized_awb = _normalize_manual_awb(awb)
    if not normalized_awb:
        return jsonify({"error": "AWB tidak valid."}), 400
    try:
        with _manuals_lock:
            rows = _read_manual_rows()
            remaining = [
                row for row in rows
                if _normalize_manual_awb(row.get("AWB")) != normalized_awb
            ]
            if len(remaining) == len(rows):
                return jsonify({"error": "AWB tidak ditemukan."}), 404
            _write_manual_rows(remaining)
        return jsonify({"message": "Manual override berhasil dihapus."})
    except Exception as exc:
        current_app.logger.exception("Gagal menghapus manual override")
        return jsonify({"error": f"Gagal memperbarui manuals.csv: {exc}"}), 500


@report_explorer_bp.route("/parquet_source_status", methods=["GET"])
def parquet_source_status():
    try:
        data = _cached_data(
            "parquet_source_status",
            STATUS_CACHE_TTL_SECONDS,
            load_parquet_source_status,
        )
        return jsonify(data)
    except Exception as exc:
        current_app.logger.error(f"Error loading parquet source status: {exc}")
        return jsonify({"error": str(exc)}), 500


@report_explorer_bp.route("/report_history_status", methods=["GET"])
def report_history_status():
    try:
        data = _cached_data(
            "report_history_status",
            STATUS_CACHE_TTL_SECONDS,
            lambda: {
                "rows":   load_report_history_rows(),
                "source": PROCESS_HISTORY_SOURCE,
                "today":  datetime.date.today().isoformat(),
            },
        )
        return jsonify(data)
    except FileNotFoundError:
        return jsonify({"error": "Process history file not found"}), 404
    except Exception as e:
        current_app.logger.error(f"Error loading process history: {e}")
        return jsonify({"error": str(e)}), 500


@report_explorer_bp.route("/daily_load_awb_count_status", methods=["GET"])
def daily_load_awb_count_status():
    try:
        def load_data():
            rows, source, start_date, end_date = load_daily_load_awb_count_rows(days=7)
            return {
                "rows": rows,
                "source": source,
                "start_date": start_date,
                "end_date": end_date,
                "total": len(rows),
            }

        data = _cached_data(
            "daily_load_awb_count_status",
            STATUS_CACHE_TTL_SECONDS,
            load_data,
        )
        return jsonify(data)
    except Exception as exc:
        current_app.logger.error(f"Error loading daily load AWB count status: {exc}")
        return jsonify({"error": str(exc)}), 500


@report_explorer_bp.route("/project_pic", methods=["GET"])
def project_pic():
    try:
        def load_data():
            rows, source_path = load_project_pic_rows()
            return {
                "rows": rows,
                "source": source_path,
                "total": len(rows),
            }

        data = _cached_data("project_pic", STATUS_CACHE_TTL_SECONDS, load_data)
        return jsonify(data)
    except FileNotFoundError as exc:
        return jsonify({"error": f"File PROJECT PIC tidak ditemukan: {exc}"}), 404
    except Exception as exc:
        current_app.logger.error(f"Error loading project PIC: {exc}")
        return jsonify({"error": str(exc)}), 500


@report_explorer_bp.route("/list_reports", methods=["GET"])
def list_reports():
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Date parameter required"}), 400

    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        data = _cached_data(
            ("list_reports", date_obj.date().isoformat()),
            REPORT_EXPLORER_CACHE_TTL_SECONDS,
            lambda: _load_list_reports_data(date_obj, date_str),
        )
        return jsonify(data)

    except Exception as e:
        current_app.logger.error(f"Error listing reports: {e}")
        return jsonify({"error": str(e)}), 500


def _load_list_reports_data(date_obj, date_str):
    report_path = _build_report_path(date_obj)

    network_ok  = os.path.exists(report_path)
    files       = []
    tags        = set()
    from_backup = False

    if network_ok:
        def add_file_entry(file_path, tag_parts, rel_path):
            tag_parts = [part for part in tag_parts if part]
            tag = " ".join(tag_parts) if tag_parts else "UNKNOWN"
            modified_time = os.path.getmtime(file_path)
            modified_dt   = datetime.datetime.fromtimestamp(modified_time)
            files.append({
                "name":        os.path.basename(file_path),
                "size":        os.path.getsize(file_path),
                "modified":    modified_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "modified_ts": modified_time,
                "tag":         tag,
                "tags":        tag_parts or ["UNKNOWN"],
                "rel_path":    rel_path.replace("\\", "/"),
                "from_backup": False,
            })
            tags.add(tag_parts[0] if tag_parts else "UNKNOWN")

        def on_walk_error(error):
            current_app.logger.warning(f"Permission error listing reports: {error}")

        for root, _, filenames in os.walk(report_path, onerror=on_walk_error):
            for filename in filenames:
                if filename.startswith("~$"):
                    continue
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, report_path)
                rel_dir = os.path.dirname(rel_path)
                tag_parts = [] if not rel_dir else rel_dir.split(os.sep)
                add_file_entry(file_path, tag_parts, rel_path)

        files.sort(key=lambda x: x["modified_ts"], reverse=True)

    if not files:
        backup_files = backup_syncer.get_backup_file_list(date_obj.date())
        if backup_files:
            files       = backup_files
            tags        = {f.get("tags", [f.get("tag", "UNKNOWN")])[0] for f in files}
            from_backup = True
            current_app.logger.info(
                f"[report_explorer] Network returned 0 files for {date_str}; "
                f"serving {len(files)} file(s) from local backup."
            )

    if not network_ok and not files:
        return {"files": [], "path": report_path, "exists": False, "from_backup": False}

    return {
        "files":       files,
        "tags":        sorted(tags),
        "path":        report_path,
        "exists":      network_ok,
        "from_backup": from_backup,
    }


@report_explorer_bp.route("/user_preferences", methods=["GET"])
def get_user_preferences():
    user_ip  = get_client_ip()
    all_prefs = _load_prefs_internal()  # read-only, no lock needed for GET
    user_data = all_prefs.get(user_ip, {"favorites": [], "recent_downloads": []})
    return jsonify(user_data)


@report_explorer_bp.route("/toggle_favorite", methods=["POST"])
def toggle_favorite():
    data     = request.get_json()
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "Filename required"}), 400

    user_ip  = get_client_ip()
    file_key = extract_file_key(filename)

    with _prefs_lock:
        all_prefs = _load_prefs_internal()
        if user_ip not in all_prefs:
            all_prefs[user_ip] = {"favorites": [], "recent_downloads": []}
        favorites = all_prefs[user_ip]["favorites"]
        if file_key in favorites:
            favorites.remove(file_key)
            is_favorite = False
        else:
            favorites.append(file_key)
            is_favorite = True
        _save_prefs_internal(all_prefs)

    return jsonify({"success": True, "is_favorite": is_favorite, "file_key": file_key})


@report_explorer_bp.route("/track_download", methods=["POST"])
def track_download():
    data     = request.get_json()
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "Filename required"}), 400

    user_ip  = get_client_ip()
    file_key = extract_file_key(filename)

    with _prefs_lock:
        all_prefs = _load_prefs_internal()
        if user_ip not in all_prefs:
            all_prefs[user_ip] = {"favorites": [], "recent_downloads": []}
        recent = all_prefs[user_ip]["recent_downloads"]
        recent = [r for r in recent if r.get("key") != file_key]
        recent.insert(0, {
            "key":       file_key,
            "filename":  filename,
            "timestamp": datetime.datetime.now().isoformat(),
        })
        all_prefs[user_ip]["recent_downloads"] = recent[:20]
        _save_prefs_internal(all_prefs)

    return jsonify({"success": True})


@report_explorer_bp.route("/download_reports", methods=["GET"])
def download_reports():
    date_str   = request.args.get("date")
    file_names = request.args.getlist("files")

    if not date_str or not file_names:
        return "Missing parameters", 400

    try:
        date_obj    = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        user_ip     = get_client_ip()
        report_path = _build_report_path(date_obj)
        base_dir    = Path(report_path).resolve()

        cleanup_old_recent_downloads(user_ip)

        def safe_join(rel_path):
            if not rel_path:
                return None
            rel_norm = os.path.normpath(rel_path)
            if os.path.isabs(rel_norm):
                return None
            target = (base_dir / rel_norm).resolve()
            if not str(target).startswith(str(base_dir)):
                return None
            return target

        # Track downloads
        try:
            with _prefs_lock:
                all_prefs = _load_prefs_internal()
                if user_ip not in all_prefs:
                    all_prefs[user_ip] = {"favorites": [], "recent_downloads": []}
                recent = all_prefs[user_ip]["recent_downloads"]
                for file_name in file_names:
                    file_key = extract_file_key(os.path.basename(file_name))
                    recent   = [r for r in recent if r.get("key") != file_key]
                    recent.insert(0, {
                        "key":       file_key,
                        "filename":  os.path.basename(file_name),
                        "path":      file_name,
                        "timestamp": datetime.datetime.now().isoformat(),
                    })
                all_prefs[user_ip]["recent_downloads"] = recent[:50]
                _save_prefs_internal(all_prefs)
        except Exception as e:
            current_app.logger.error(f"Error tracking download: {e}")

        # Single file
        if len(file_names) == 1:
            file_rel  = file_names[0]
            file_path = safe_join(file_rel)
            if file_path and file_path.exists():
                return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_rel))
            backup_path = backup_syncer.resolve_backup_file(date_obj.date(), file_rel)
            if backup_path:
                return send_file(backup_path, as_attachment=True, download_name=os.path.basename(file_rel))
            return "File not found", 404

        # Multiple files → ZIP
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_name in file_names:
                file_path = safe_join(file_name)
                if not (file_path and file_path.exists()):
                    bp = backup_syncer.resolve_backup_file(date_obj.date(), file_name)
                    if bp:
                        file_path = Path(bp)
                if file_path and file_path.exists():
                    zf.write(file_path, arcname=os.path.basename(file_name))

        memory_file.seek(0)
        zip_name = f"reports_{date_obj.strftime('%Y%m%d')}.zip"
        return send_file(
            memory_file,
            mimetype="application/zip",
            as_attachment=True,
            download_name=zip_name,
        )

    except Exception as e:
        current_app.logger.error(f"Error downloading reports: {e}")
        return str(e), 500


@report_explorer_bp.route("/backup_status", methods=["GET"])
def backup_status():
    data = _cached_data("backup_status", REPORT_EXPLORER_CACHE_TTL_SECONDS, backup_syncer.get_status)
    return jsonify(data)
