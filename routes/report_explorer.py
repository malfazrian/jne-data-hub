"""
Blueprint: Report Explorer
Routes: /report_explorer, /parquet_source_status, /report_history_status,
        /list_reports, /user_preferences, /toggle_favorite,
        /track_download, /download_reports, /backup_status
"""
import os
import json
import datetime
import io
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
    _BULAN, _prefs_lock, _load_prefs_internal, _save_prefs_internal,
    get_client_ip, is_admin_request,
    extract_file_key, cleanup_old_recent_downloads,
)

report_explorer_bp = Blueprint("report_explorer", __name__)


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
    """Scan last 3 months of source Excel dirs and compare against processed_files.json."""
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

    for yr, mo in months_to_check:
        label    = f"{_BULAN[mo]} {yr}"
        dir_name = f"{mo}. {_BULAN[mo]} {yr}"
        dir_path = os.path.join(PARQUET_SOURCE_BASE, str(yr), dir_name)
        dir_exists = os.path.isdir(dir_path)

        mo_total = mo_done = mo_failed = mo_unprocessed = 0

        if dir_exists:
            try:
                for entry in os.scandir(dir_path):
                    if not entry.is_file():
                        continue
                    if entry.name.startswith("~$"):
                        continue
                    if not entry.name.lower().endswith(".xlsx"):
                        continue
                    mo_total += 1
                    norm = os.path.normcase(entry.path)
                    rec  = processed_log.get(norm)
                    if rec is None:
                        mo_unprocessed += 1
                    elif rec.get("status") == "done":
                        mo_done += 1
                    elif rec.get("status") == "failed":
                        mo_failed += 1
                    else:
                        mo_unprocessed += 1
            except Exception as exc:
                current_app.logger.error(f"Error scanning {dir_path}: {exc}")

        grand_total       += mo_total
        grand_done        += mo_done
        grand_failed      += mo_failed
        grand_unprocessed += mo_unprocessed

        month_rows.append({
            "label":      label,
            "dir_exists": dir_exists,
            "total":      mo_total,
            "done":       mo_done,
            "failed":     mo_failed,
            "unprocessed": mo_unprocessed,
        })

    return {
        "months":           month_rows,
        "total_excel":      grand_total,
        "total_done":       grand_done,
        "total_failed":     grand_failed,
        "total_unprocessed": grand_unprocessed,
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
    return render_template("report_viewer.html", today=today, is_admin=is_admin_request())


@report_explorer_bp.route("/parquet_source_status", methods=["GET"])
def parquet_source_status():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    try:
        data = load_parquet_source_status()
        return jsonify(data)
    except Exception as exc:
        current_app.logger.error(f"Error loading parquet source status: {exc}")
        return jsonify({"error": str(exc)}), 500


@report_explorer_bp.route("/report_history_status", methods=["GET"])
def report_history_status():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    try:
        rows = load_report_history_rows()
        return jsonify({
            "rows":   rows,
            "source": PROCESS_HISTORY_SOURCE,
            "today":  datetime.date.today().isoformat(),
        })
    except FileNotFoundError:
        return jsonify({"error": "Process history file not found"}), 404
    except Exception as e:
        current_app.logger.error(f"Error loading process history: {e}")
        return jsonify({"error": str(e)}), 500


@report_explorer_bp.route("/project_pic", methods=["GET"])
def project_pic():
    try:
        rows, source_path = load_project_pic_rows()
        return jsonify({
            "rows": rows,
            "source": source_path,
            "total": len(rows),
        })
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
        date_obj    = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        report_path = _build_report_path(date_obj)

        network_ok  = os.path.exists(report_path)
        files       = []
        tags        = set()
        from_backup = False

        if network_ok:
            def add_file_entry(file_path, tag, rel_path):
                modified_time = os.path.getmtime(file_path)
                modified_dt   = datetime.datetime.fromtimestamp(modified_time)
                files.append({
                    "name":        os.path.basename(file_path),
                    "size":        os.path.getsize(file_path),
                    "modified":    modified_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_ts": modified_time,
                    "tag":         tag,
                    "rel_path":    rel_path.replace("\\", "/"),
                    "from_backup": False,
                })
                tags.add(tag)

            for entry in os.scandir(report_path):
                if entry.is_file():
                    if entry.name.startswith("~$"):
                        continue
                    add_file_entry(entry.path, "UNKNOWN", entry.name)
                elif entry.is_dir():
                    tag = entry.name
                    for item in os.scandir(entry.path):
                        if not item.is_file() or item.name.startswith("~$"):
                            continue
                        rel_path = os.path.join(tag, item.name)
                        add_file_entry(item.path, tag, rel_path)

            files.sort(key=lambda x: x["modified_ts"], reverse=True)

        if not files:
            backup_files = backup_syncer.get_backup_file_list(date_obj.date())
            if backup_files:
                files       = backup_files
                tags        = {f["tag"] for f in files}
                from_backup = True
                current_app.logger.info(
                    f"[report_explorer] Network returned 0 files for {date_str}; "
                    f"serving {len(files)} file(s) from local backup."
                )

        if not network_ok and not files:
            return jsonify({"files": [], "path": report_path, "exists": False, "from_backup": False})

        return jsonify({
            "files":       files,
            "tags":        sorted(tags),
            "path":        report_path,
            "exists":      network_ok,
            "from_backup": from_backup,
        })

    except Exception as e:
        current_app.logger.error(f"Error listing reports: {e}")
        return jsonify({"error": str(e)}), 500


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
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(backup_syncer.get_status())
