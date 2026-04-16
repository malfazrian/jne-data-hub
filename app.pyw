import json
import time
import threading
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, Response, jsonify, stream_with_context
from threading import Thread
from pathlib import Path
from scripts.ref_reader import load_projects
from scripts.query_performance_custom import ProjectProcessor
from scripts.query_performance_parquet import ProjectProcessorParquet
from scripts.apex_query_by_id import ApexRequestProcessor
import os
import zipfile
import io
import uuid
import shutil
import traceback
import datetime
import socket
from waitress import serve
from scripts.utils import auto_update_project_reference
from scripts.apex_query import ApexQueryJob
from scripts.lists import criteria_lists

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_file_path):
    """Load simple KEY=VALUE pairs from .env into os.environ (without overriding existing env vars)."""
    if not os.path.exists(env_file_path):
        return

    try:
        with open(env_file_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        print(f"[WARN] Failed to load .env file: {exc}")


def env_int(name, default_value):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default_value
    try:
        return int(value)
    except ValueError:
        return default_value


def env_csv(name, default_values):
    value = os.getenv(name, "")
    if not value.strip():
        return default_values
    return [item.strip() for item in value.split(",") if item.strip()]


ENV_FILE = os.path.join(BASE_DIR, ".env")
load_env_file(ENV_FILE)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-before-production")
base_path = os.getenv("CSV_BASE_PATH", "")
base_path_parquet = os.getenv("PARQUET_BASE_PATH", "")
archive_dir = os.path.join(BASE_DIR, "output")
os.makedirs(archive_dir, exist_ok=True)

# User preferences storage
USER_PREFS_FILE = os.path.join(BASE_DIR, "data", "user_preferences.json")
os.makedirs(os.path.dirname(USER_PREFS_FILE), exist_ok=True)
PROCESS_HISTORY_SOURCE = os.getenv("PROCESS_HISTORY_SOURCE", "")
PARQUET_PROCESSED_FILE = os.getenv("PARQUET_PROCESSED_FILE", "")
PARQUET_SOURCE_BASE = os.getenv("PARQUET_SOURCE_BASE", "")
REPORT_EXPLORER_BASE = os.getenv("REPORT_EXPLORER_BASE", "")
APEX_DEFAULT_HOSTS = env_csv("APEX_DEFAULT_HOSTS", [])
_BULAN = {
    1: "JANUARI", 2: "FEBRUARI", 3: "MARET", 4: "APRIL",
    5: "MEI", 6: "JUNI", 7: "JULI", 8: "AGUSTUS",
    9: "SEPTEMBER", 10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER"
}

# File lock untuk prevent race condition
_prefs_lock = threading.Lock()

def _load_prefs_internal():
    """Internal: Load preferences without locking (call only within locked context)"""
    if os.path.exists(USER_PREFS_FILE):
        try:
            with open(USER_PREFS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            app.logger.error(f"Error loading preferences: {e}")
            return {}
    return {}

def _save_prefs_internal(data):
    """Internal: Save preferences without locking (call only within locked context)"""
    try:
        # Write to temp file first, then atomic rename
        temp_file = USER_PREFS_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Atomic replace (Windows-safe)
        if os.path.exists(USER_PREFS_FILE):
            os.replace(temp_file, USER_PREFS_FILE)
        else:
            os.rename(temp_file, USER_PREFS_FILE)
    except Exception as e:
        app.logger.error(f"Error saving preferences: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass

def load_user_preferences():
    """Load user preferences from JSON file (thread-safe)"""
    with _prefs_lock:
        return _load_prefs_internal()

def save_user_preferences(data):
    """Save user preferences to JSON file (thread-safe)"""
    with _prefs_lock:
        _save_prefs_internal(data)

def extract_file_key(filename):
    """Extract key from filename (before dash)"""
    # Remove extension first
    name_without_ext = os.path.splitext(filename)[0]
    # Split by dash and take first part
    if ' - ' in name_without_ext:
        return name_without_ext.split(' - ')[0].strip()
    return name_without_ext.strip()

def cleanup_old_recent_downloads(user_ip):
    """Remove recent downloads older than 1 day for specific user"""
    with _prefs_lock:
        all_prefs = _load_prefs_internal()
        if user_ip not in all_prefs or 'recent_downloads' not in all_prefs[user_ip]:
            return
        
        now = datetime.datetime.now()
        recent = all_prefs[user_ip]['recent_downloads']
        
        # Filter: keep only downloads from today
        filtered = []
        for item in recent:
            try:
                item_date = datetime.datetime.fromisoformat(item.get('timestamp', ''))
                # Check if same date (year, month, day)
                if (item_date.year == now.year and 
                    item_date.month == now.month and 
                    item_date.day == now.day):
                    filtered.append(item)
            except (ValueError, TypeError):
                # Skip invalid timestamps
                continue
        
        all_prefs[user_ip]['recent_downloads'] = filtered
        _save_prefs_internal(all_prefs)
        
        removed_count = len(recent) - len(filtered)
        if removed_count > 0:
            app.logger.info(f"Cleaned up {removed_count} old recent downloads for {user_ip}")

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr or 'unknown'

def get_server_ips():
    """Collect IP addresses bound to this host for lightweight admin checks."""
    ips = {"127.0.0.1", "::1"}
    for host_name in {socket.gethostname(), socket.getfqdn()}:
        if not host_name:
            continue
        try:
            _, _, resolved_ips = socket.gethostbyname_ex(host_name)
            ips.update(ip for ip in resolved_ips if ip)
        except socket.gaierror:
            continue
    return ips

def is_admin_request():
    """Allow admin-only UI for requests coming from the same machine as the web host."""
    client_ip = get_client_ip()
    host_value = (request.host or "").split(":")[0].strip("[]").lower()
    localhost_aliases = {"localhost", "127.0.0.1", "::1"}
    server_ips = get_server_ips()

    if client_ip in server_ips:
        return True
    if client_ip in localhost_aliases and host_value in localhost_aliases.union(server_ips):
        return True
    if host_value and client_ip == host_value:
        return True
    return False

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
        icon = "✅"
        label = "Update hari ini"
    elif age_days <= 7:
        icon = "❌"
        label = f"Belum update {age_days} hari"
    else:
        icon = "💤"
        label = f"Belum update {age_days} hari"

    return {
        "icon": icon,
        "label": label,
        "date": date_value.isoformat()
    }

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
            is_match = edit_normalized == alias or edit_normalized.startswith(f"{alias} - ")
            if not is_match:
                continue

            candidate = (len(alias), query_name, query_date)
            if best_match is None or candidate[0] > best_match[0]:
                best_match = candidate

    if best_match is None:
        return None
    return best_match[2]

def load_parquet_source_status():
    """Scan last 3 months of source Excel dirs and compare against processed_files.json."""
    today = datetime.date.today()

    # Build last-3-month list
    months_to_check = []
    year, month = today.year, today.month
    for _ in range(3):
        months_to_check.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    # Load and normalise the processed-files log
    processed_log = {}
    log_exists = os.path.exists(PARQUET_PROCESSED_FILE)
    if log_exists:
        try:
            with open(PARQUET_PROCESSED_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for k, v in raw.items():
                processed_log[os.path.normcase(k)] = v
        except Exception as exc:
            app.logger.error(f"Error reading parquet processed log: {exc}")

    month_rows = []
    grand_total = grand_done = grand_failed = grand_unprocessed = 0

    for yr, mo in months_to_check:
        label = f"{_BULAN[mo]} {yr}"
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
                    rec = processed_log.get(norm)
                    if rec is None:
                        mo_unprocessed += 1
                    elif rec.get("status") == "done":
                        mo_done += 1
                    elif rec.get("status") == "failed":
                        mo_failed += 1
                    else:
                        mo_unprocessed += 1
            except Exception as exc:
                app.logger.error(f"Error scanning {dir_path}: {exc}")

        grand_total += mo_total
        grand_done += mo_done
        grand_failed += mo_failed
        grand_unprocessed += mo_unprocessed

        month_rows.append({
            "label": label,
            "dir_exists": dir_exists,
            "total": mo_total,
            "done": mo_done,
            "failed": mo_failed,
            "unprocessed": mo_unprocessed,
        })

    return {
        "months": month_rows,
        "total_excel": grand_total,
        "total_done": grand_done,
        "total_failed": grand_failed,
        "total_unprocessed": grand_unprocessed,
        "log_source": PARQUET_PROCESSED_FILE,
        "log_exists": log_exists,
    }


def load_report_history_rows():
    """Build report history rows from edit entries and inherit query status from the best matching query entry."""
    if not os.path.exists(PROCESS_HISTORY_SOURCE):
        raise FileNotFoundError(PROCESS_HISTORY_SOURCE)

    with open(PROCESS_HISTORY_SOURCE, "r", encoding="utf-8") as file:
        history = json.load(file)

    query_dates = {}
    edit_dates = {}

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
            existing_date = query_dates.get(report_name)
            if existing_date is None or (parsed_date and parsed_date > existing_date):
                query_dates[report_name] = parsed_date
        else:
            existing_date = edit_dates.get(report_name)
            if existing_date is None or (parsed_date and parsed_date > existing_date):
                edit_dates[report_name] = parsed_date

    today = datetime.date.today()
    result = []
    for report_name in sorted(edit_dates):
        edit_date = edit_dates[report_name]
        query_date = find_best_query_match(report_name, query_dates)
        result.append({
            "report_name": report_name,
            "query": build_history_status(query_date, today),
            "edit": build_history_status(edit_date, today),
        })

    return result

def clean_old_outputs(output_dir, max_age_days=1):
    now = datetime.datetime.now()
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            age_days = (now - mtime).days
            if age_days >= max_age_days:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
        except Exception as e:
            print(f"Error cleaning {path}: {e}")

def validate_progress_status(request_id):
    """Periksa apakah file hasil masih ada; kalau tidak, hapus dari dict."""
    status = progress_status.get(request_id)
    if not status:
        return None

    files = status.get("files", [])
    # Kalau semua file sudah dihapus, hapus entry progress_status
    if status.get("done") and all(not os.path.exists(f) for f in files):
        progress_status.pop(request_id, None)
        app.logger.info(f"Progress {request_id} dihapus karena semua file sudah tidak ada.")
        return None

    return status

def cleanup_old_results():
    expired = []
    for req_id, status in list(progress_status.items()):
        files = status.get("files", [])
        if all(not os.path.exists(f) for f in files):
            expired.append(req_id)

    for req_id in expired:
        progress_status.pop(req_id, None)
        app.logger.info(f"Cleaned up expired request: {req_id}")

# PERFORMANCE
progress_status = {}  # {request_id: {"current": int, "total": int, "files": [...], "done": bool}}

def start_processing(start, end, project_lists_json, full=False, report=False,
                     project_status=None, criteria_lists=None, filter_categories=None,
                     data_source="parquet", start_date=None, end_date=None):
    """
    Fungsi pemrosesan utama (jalan di thread background).
    Kini mendukung filter_categories (hanya untuk mode full).
    """
    # Bersihkan output lama
    clean_old_outputs(archive_dir, max_age_days=1)

    # =====================
    # 🔹 Validasi input periode
    # =====================
    def yymm_to_date(val):
        if not val or len(val) != 4 or not val.isdigit():
            return None
        year = int("20" + val[:2])
        month = int(val[2:])
        try:
            return year, month
        except:
            return None

    def parse_ymd(val):
        if not val:
            return None
        try:
            return datetime.datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return None

    start_dt = parse_ymd(start_date)
    end_dt = parse_ymd(end_date)

    if start_date or end_date:
        if not start_dt or not end_dt:
            return {"success": False, "message": "Format tanggal salah! Gunakan YYYY-MM-DD."}
        if start_dt > end_dt:
            return {"success": False, "message": "Tanggal awal tidak boleh lebih baru dari tanggal akhir!"}
        if not start or not end:
            start = start_dt.strftime("%y%m")
            end = end_dt.strftime("%y%m")
    else:
        start_date = yymm_to_date(start)
        end_date = yymm_to_date(end)
        if not start_date or not end_date:
            return {"success": False, "message": "Format periode salah! Gunakan YYMM."}

        if (start_date[0], start_date[1]) > (end_date[0], end_date[1]):
            return {"success": False, "message": "Bulan awal tidak boleh lebih baru dari bulan akhir!"}

    if not project_lists_json.strip():
        return {"success": False, "message": "Daftar project tidak boleh kosong!"}

    try:
        project_lists = json.loads(project_lists_json)
    except json.JSONDecodeError:
        return {"success": False, "message": "Format data project tidak valid!"}

    # =====================
    # 🔹 Setup request dan progress
    # =====================
    request_id = str(uuid.uuid4())
    user_archive_dir = os.path.join(archive_dir, "master_data", request_id)
    os.makedirs(user_archive_dir, exist_ok=True)

    progress_status[request_id] = {
        "current": 0,
        "total": 1,
        "files": [],
        "done": False,
        "error": None
    }

    # =====================
    # 🔹 Jalankan proses di background
    # =====================
    def background_job():
        try:
            source = (data_source or "parquet").strip().lower()
            if source == "csv":
                processor = ProjectProcessor(
                    project_lists=project_lists,
                    start_yy_mm=start,
                    end_yy_mm=end,
                    base_path=base_path,
                    archive_dir=user_archive_dir,
                    progress_dict=progress_status,
                    full=full,
                    report=report,
                    status=project_status,
                    criteria_lists=criteria_lists,
                    start_date=start_dt,
                    end_date=end_dt
                )
            else:
                processor = ProjectProcessorParquet(
                    project_lists=project_lists,
                    start_yy_mm=start,
                    end_yy_mm=end,
                    base_path=base_path_parquet,
                    archive_dir=user_archive_dir,
                    progress_dict=progress_status,
                    full=full,
                    report=report,
                    status=project_status,
                    criteria_lists=criteria_lists,
                    start_date=start_dt,
                    end_date=end_dt
                )

            # ✅ Tambahkan filter_categories hanya jika mode full dan ada datanya
            saved_files = processor.run(
                request_id=request_id,
                filter_categories=filter_categories if full else None
            )

            if not saved_files:
                shutil.rmtree(user_archive_dir, ignore_errors=True)
                progress_status[request_id] = {
                    "current": 100,
                    "total": 1,
                    "files": [],
                    "done": True
                }
                return

            # 🔹 MULTI PROJECT → buat ZIP SEKARANG
            if len(saved_files) > 1:
                zip_path = os.path.join(user_archive_dir, "projects_performance.zip")

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for fpath in saved_files:
                        if os.path.exists(fpath):
                            zipf.write(fpath, arcname=os.path.basename(fpath))

                # hanya simpan ZIP sebagai hasil
                progress_status[request_id]["files"] = [zip_path]

            else:
                # single project → langsung file
                progress_status[request_id]["files"] = saved_files


            progress_status[request_id].update({
                "current": 100,
                "done": True
            })

        except Exception as e:
            shutil.rmtree(user_archive_dir, ignore_errors=True)
            progress_status[request_id] = {
                "current": 1, "total": 1, "files": [], "done": True, "error": str(e)
            }
            print(f"❌ Error background job: {e}")
            print(traceback.format_exc())

    thread = threading.Thread(target=background_job, daemon=True)
    thread.start()

    # =====================
    # 🔹 Response awal ke FE
    # =====================
    mode_text = "FULL DATA" if full else "REPORT" if report else "PERFORMANCE"
    return {
        "success": True,
        "message": f"Proses {mode_text} sedang berjalan. Silakan tunggu...",
        "request_id": request_id
    }

# ROUTES
@app.route("/", methods=["GET"])
def index():
    projects, project_names = load_projects()
    return render_template("index.html", projects_json=projects, project_names=project_names, mode="normal")

@app.route("/full", methods=["GET"])
def full_index():
    projects, project_names = load_projects()
    return render_template("index.html", projects_json=projects, project_names=project_names, mode="full")

@app.route("/report", methods=["GET"])
def report_index():
    """Halaman utama untuk Report Mode"""
    projects, project_names = load_projects()
    return render_template(
        "index.html",
        projects_json=projects,
        project_names=project_names,
        mode="report"
    )

@app.route("/apex_uploader", methods=["GET"])
def apex_uploader():
    return render_template("apex_uploader.html")

@app.route("/apex_requester", methods=["GET"])
def apex_requester():
    return render_template("apex_requester.html")

@app.route("/report_explorer", methods=["GET"])
def report_explorer():
    user_ip = get_client_ip()
    cleanup_old_recent_downloads(user_ip)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return render_template("report_viewer.html", today=today, is_admin=is_admin_request())

@app.route("/parquet_source_status", methods=["GET"])
def parquet_source_status():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    try:
        data = load_parquet_source_status()
        return jsonify(data)
    except Exception as exc:
        app.logger.error(f"Error loading parquet source status: {exc}")
        return jsonify({"error": str(exc)}), 500


@app.route("/report_history_status", methods=["GET"])
def report_history_status():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403

    try:
        rows = load_report_history_rows()
        return jsonify({
            "rows": rows,
            "source": PROCESS_HISTORY_SOURCE,
            "today": datetime.date.today().isoformat()
        })
    except FileNotFoundError:
        return jsonify({"error": "Process history file not found"}), 404
    except Exception as e:
        app.logger.error(f"Error loading process history: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/list_reports", methods=["GET"])
def list_reports():
    """List all report files for a given date"""
    date_str = request.args.get("date")  # Format: YYYY-MM-DD
    
    if not date_str:
        return jsonify({"error": "Date parameter required"}), 400
    
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        
        # Build path based on date
        # Example: {REPORT_EXPLORER_BASE}\ALL REPORT GABUNGAN\01. JANUARI 26\28 01 26\trial BOT
        bulan_dict = {
            1: "JANUARI", 2: "FEBRUARI", 3: "MARET", 4: "APRIL",
            5: "MEI", 6: "JUNI", 7: "JULI", 8: "AGUSTUS",
            9: "SEPTEMBER", 10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER"
        }
        
        bulan_nama = bulan_dict[date_obj.month]
        tahun_2digit = str(date_obj.year)[-2:]
        bulan_num = f"{date_obj.month:02d}"
        tanggal_num = f"{date_obj.day:02d}"
        
        # Path pattern: hardcoded base + dynamic date folders
        report_path = os.path.join(
            REPORT_EXPLORER_BASE,
            "ALL REPORT GABUNGAN",
            f"{bulan_num}. {bulan_nama} {tahun_2digit}",
            f"{tanggal_num} {bulan_num} {tahun_2digit}",
            "trial BOT"
        )
        
        if not os.path.exists(report_path):
            return jsonify({"files": [], "path": report_path, "exists": False})
        
        files = []
        tags = set()

        def add_file_entry(file_path, tag, rel_path):
            modified_time = os.path.getmtime(file_path)
            modified_dt = datetime.datetime.fromtimestamp(modified_time)
            files.append({
                "name": os.path.basename(file_path),
                "size": os.path.getsize(file_path),
                "modified": modified_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "modified_ts": modified_time,
                "tag": tag,
                "rel_path": rel_path.replace("\\", "/")
            })
            tags.add(tag)

        for entry in os.scandir(report_path):
            if entry.is_file():
                if entry.name.startswith('~$'):
                    continue
                add_file_entry(entry.path, "UNKNOWN", entry.name)
            elif entry.is_dir():
                tag = entry.name
                for item in os.scandir(entry.path):
                    if not item.is_file():
                        continue
                    if item.name.startswith('~$'):
                        continue
                    rel_path = os.path.join(tag, item.name)
                    add_file_entry(item.path, tag, rel_path)
        
        # Sort by modified date descending (newest first)
        files.sort(key=lambda x: x["modified_ts"], reverse=True)
        
        return jsonify({
            "files": files,
            "tags": sorted(tags),
            "path": report_path,
            "exists": True
        })
    
    except Exception as e:
        app.logger.error(f"Error listing reports: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/user_preferences", methods=["GET"])
def get_user_preferences():
    """Get user preferences (favorites and recent downloads)"""
    user_ip = get_client_ip()
    all_prefs = load_user_preferences()
    user_data = all_prefs.get(user_ip, {
        "favorites": [],
        "recent_downloads": []
    })
    return jsonify(user_data)

@app.route("/toggle_favorite", methods=["POST"])
def toggle_favorite():
    """Toggle favorite status for a file"""
    data = request.get_json()
    filename = data.get("filename")
    
    if not filename:
        return jsonify({"error": "Filename required"}), 400
    
    user_ip = get_client_ip()
    file_key = extract_file_key(filename)
    
    # Atomic operation dengan lock
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
    
    return jsonify({
        "success": True,
        "is_favorite": is_favorite,
        "file_key": file_key
    })

@app.route("/track_download", methods=["POST"])
def track_download():
    """Track when user downloads a file"""
    data = request.get_json()
    filename = data.get("filename")
    
    if not filename:
        return jsonify({"error": "Filename required"}), 400
    
    user_ip = get_client_ip()
    file_key = extract_file_key(filename)
    
    # Atomic operation dengan lock
    with _prefs_lock:
        all_prefs = _load_prefs_internal()
        if user_ip not in all_prefs:
            all_prefs[user_ip] = {"favorites": [], "recent_downloads": []}
        
        recent = all_prefs[user_ip]["recent_downloads"]
        
        # Remove if already exists
        recent = [r for r in recent if r.get("key") != file_key]
        
        # Add to beginning
        recent.insert(0, {
            "key": file_key,
            "filename": filename,
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Keep only last 20
        recent = recent[:20]
        
        all_prefs[user_ip]["recent_downloads"] = recent
        _save_prefs_internal(all_prefs)
    
    return jsonify({"success": True})

@app.route("/download_reports", methods=["GET"])
def download_reports():
    """Download single or multiple report files"""
    date_str = request.args.get("date")
    file_names = request.args.getlist("files")
    
    if not date_str or not file_names:
        return "Missing parameters", 400
    
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        user_ip = get_client_ip()
        
        # Cleanup old recent downloads before tracking new ones
        cleanup_old_recent_downloads(user_ip)
        
        bulan_dict = {
            1: "JANUARI", 2: "FEBRUARI", 3: "MARET", 4: "APRIL",
            5: "MEI", 6: "JUNI", 7: "JULI", 8: "AGUSTUS",
            9: "SEPTEMBER", 10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER"
        }
        
        bulan_nama = bulan_dict[date_obj.month]
        tahun_2digit = str(date_obj.year)[-2:]
        bulan_num = f"{date_obj.month:02d}"
        tanggal_num = f"{date_obj.day:02d}"
        
        # Path pattern: hardcoded base + dynamic date folders
        report_path = os.path.join(
            REPORT_EXPLORER_BASE,
            "ALL REPORT GABUNGAN",
            f"{bulan_num}. {bulan_nama} {tahun_2digit}",
            f"{tanggal_num} {bulan_num} {tahun_2digit}",
            "trial BOT"
        )
        
        base_dir = Path(report_path).resolve()

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

        # Track downloads (both single and multiple)
        try:
            with _prefs_lock:
                all_prefs = _load_prefs_internal()
                if user_ip not in all_prefs:
                    all_prefs[user_ip] = {"favorites": [], "recent_downloads": []}
                recent = all_prefs[user_ip]["recent_downloads"]

                # Add each file to recent downloads
                for file_name in file_names:
                    file_key = extract_file_key(os.path.basename(file_name))
                    # Remove if already exists
                    recent = [r for r in recent if r.get("key") != file_key]
                    # Add to beginning
                    recent.insert(0, {
                        "key": file_key,
                        "filename": os.path.basename(file_name),
                        "path": file_name,
                        "timestamp": datetime.datetime.now().isoformat()
                    })

                # Keep only last 50 (will be filtered to today's only on next cleanup)
                all_prefs[user_ip]["recent_downloads"] = recent[:50]
                _save_prefs_internal(all_prefs)
        except Exception as e:
            app.logger.error(f"Error tracking download: {e}")

        # Single file download
        if len(file_names) == 1:
            file_rel = file_names[0]
            file_path = safe_join(file_rel)
            if file_path and file_path.exists():
                return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_rel))
            return "File not found", 404
        
        # Multiple files - create ZIP
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_name in file_names:
                file_path = safe_join(file_name)
                if file_path and file_path.exists():
                    zf.write(file_path, arcname=os.path.basename(file_name))
        
        memory_file.seek(0)
        zip_name = f"reports_{date_obj.strftime('%Y%m%d')}.zip"
        
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_name
        )
    
    except Exception as e:
        app.logger.error(f"Error downloading reports: {e}")
        return str(e), 500

@app.route("/run", methods=["POST"])
def run():
    data = request.get_json()
    return start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        full=False,   # normal mode
        project_status=data.get("project_status"),
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date")
    )

@app.route("/run_full", methods=["POST"])
def run_full():
    data = request.get_json()

    # 🔹 Ambil kategori (opsional)
    filter_categories = data.get("filter_categories", [])

    return start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        full=True,
        project_status=data.get("project_status"),
        filter_categories=filter_categories,
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date")
    )

@app.route("/run_report", methods=["POST"])
def run_report():    
    data = request.get_json()
    return start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        report=True,
        project_status=data.get("project_status"),
        criteria_lists=criteria_lists,
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date")
    )

@app.route("/download/<request_id>")
def download(request_id):
    status = progress_status.get(request_id)
    if not status or not status.get("done"):
        flash("File belum siap.", "danger")
        return redirect(url_for("index"))

    files = status.get("files", [])
    if not files:
        flash("File tidak tersedia.", "danger")
        return redirect(url_for("index"))

    return send_file(files[0], as_attachment=True)

@app.route("/progress/<request_id>")
def progress(request_id):
    def generate():
        last_sent = -1
        while True:
            status = progress_status.get(request_id)
            if not status:
                yield f"data: 0\n\n"
                time.sleep(1)
                continue

            current = status.get("current", 0)
            total = status.get("total", 1) or 1
            percent = int(current / total * 100)

            if percent != last_sent:
                yield f"data: {percent}\n\n"
                last_sent = percent

            if status.get("done", False):
                break

            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")

@app.route("/progress_status/<request_id>")
def progress_status_api(request_id):
    status = validate_progress_status(request_id)
    if not status:
        return jsonify({
            "exists": False,
            "done": False,
            "percent": 0,
            "error": "File hasil sudah dihapus atau request tidak ditemukan."
        }), 410

    current = status.get("current", 0)
    total = status.get("total", 1) or 1
    percent = int(current / total * 100)

    return jsonify({
        "exists": True,
        "done": status.get("done", False),
        "percent": percent,
        "files": status.get("files", [])
    })

def auto_cleanup_thread():
    while True:
        cleanup_old_results()
        time.sleep(3600)  # bersihkan tiap 1 jam

threading.Thread(target=auto_cleanup_thread, daemon=True).start()

# APEX UPLOADER
job_status = {}

@app.route("/start_job", methods=["POST"])
def start_job():
    username = request.form.get("username")
    password = request.form.get("password")
    host = request.form.get("host")
    files = request.files.getlist("source_files")

    try:
        chunk_size = int(request.form.get("chunk", 9999))
        if chunk_size <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Nilai chunk harus berupa angka positif."}), 400

    if not username or not password or len(files) == 0 or not host:
        return jsonify({"error": "Semua field wajib diisi."}), 400

    job_id = str(uuid.uuid4())
    job_dir = Path(archive_dir) / "uploader" / job_id
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file in files:
        file_path = source_dir / file.filename
        try:
            file.save(file_path)
            if file_path.exists():
                saved_files.append(file_path.name)
            else:
                print(f"❌ Gagal simpan (tidak ditemukan setelah save): {file_path}")
        except Exception as e:
            print(f"⚠️ Error saat menyimpan {file.filename}: {e}")

    if not saved_files:
        return jsonify({"error": "Gagal menyimpan file ke server."}), 500

    # inisialisasi progress
    job_status[job_id] = {"progress": 0, "log": [], "done": False, "cancelled": False}

    def run_job():
        job = ApexQueryJob(
            base_dir=str(job_dir),
            username=username,
            password=password,
            request_id=job_id,
            status_dict=job_status
        )
        job.selected_host = host 
        job.chunk_size = chunk_size
        result = job.run()
        job_status[job_id]["log"].append(result.get("message", "Selesai."))
        job_status[job_id]["done"] = True
        job_status[job_id]["progress"] = 100

    Thread(target=run_job, daemon=True).start()
    return jsonify({"job_id": job_id})

@app.route("/download-apex/<job_id>")
def download_result(job_id):
    job_dir = Path(archive_dir) / "uploader" / job_id
    result_file = job_dir / "downloads" / "Updated AWB.csv"

    if not result_file.exists():
        print("🔍 Folder contents:", list(job_dir.glob("*")))
        return jsonify({"error": "File hasil tidak ditemukan."}), 404

    return send_file(result_file, as_attachment=True)

@app.route("/progress-apex/<job_id>")
def progress_apex_sse(job_id):
    if job_id not in job_status:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    @stream_with_context
    def generate():
        last_state = None
        while True:
            status = job_status.get(job_id)
            if not status:
                yield f"data: {json.dumps({'error': 'Job tidak ditemukan'})}\n\n"
                break

            data = {
                "progress": status.get("progress", 0),
                "log": status.get("log", []),
                "done": status.get("done", False),
            }

            # Kirim hanya jika ada update baru
            msg = json.dumps(data)
            if msg != last_state:
                yield f"data: {msg}\n\n"
                last_state = msg

            if status.get("done", False):
                break

            time.sleep(1)  # interval refresh di server

    return Response(generate(), mimetype="text/event-stream")

@app.route("/check-job/<job_id>")
def check_job(job_id):
    job = job_status.get(job_id)
    if not job:
        return jsonify({"exists": False})
    return jsonify({"exists": True, "done": job.get("done", False)})

@app.route("/cancel-job/<job_id>", methods=["POST"])
def cancel_job(job_id):
    job = job_status.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    job["cancelled"] = True
    job["log"].append("⛔ Job dibatalkan oleh user.")
    return jsonify({"success": True})

# APEX REQUESTER
session_tasks = {}

@app.route("/start-request", methods=["POST"])
def start_process():
    data = request.json
    task_id = str(uuid.uuid4())
    download_dir = Path(archive_dir) / "requester" / task_id
    os.makedirs(download_dir, exist_ok=True)

    # Ambil host dari frontend (kalau kosong, fallback ke default)
    host = data.get("host")
    apex_hosts = [host] if host else APEX_DEFAULT_HOSTS

    processor = ApexRequestProcessor(
        download_dir=str(download_dir),
        list_customer_ids=data["list_customer_ids"],
        tanggal_awal=data["tanggal_awal"],
        tanggal_akhir=data.get("tanggal_akhir"),
        apex_hosts=apex_hosts,  
        username=data["username"],
        password=data["password"],
        task_id=task_id,
        task_store=session_tasks
    )

    session_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "tracker": processor.tracker.rows,
        "cancelled": False
    }

    def run_job():
        total = len(processor.tracker.rows)

        def update_progress(pct, tracker_rows):
            session_tasks[task_id]["progress"] = pct
            session_tasks[task_id]["tracker"] = tracker_rows

        processor.progress_callback = update_progress

        for host in processor.apex_hosts:
            if processor.is_cancelled():
                print(f"⛔ Task {task_id} dibatalkan sebelum koneksi host.")
                session_tasks[task_id]["status"] = "cancelled"
                return

            success = processor._connect_and_request(host)
            if processor.is_cancelled():
                session_tasks[task_id]["status"] = "cancelled"
                return

            if success:
                session_tasks[task_id]["status"] = "finished"
                session_tasks[task_id]["progress"] = 100
                processor.merge_results()
                return
            else:
                print(f"Gagal di host {host}, mencoba host berikutnya...")

        # Kalau semua host gagal
        if not processor.is_cancelled():
            session_tasks[task_id]["status"] = "finished"
            session_tasks[task_id]["progress"] = 100

    Thread(target=run_job).start()

    return jsonify({"task_id": task_id})

@app.route("/progress-request/<task_id>")
def get_progress(task_id):
    task_info = session_tasks.get(task_id)
    if not task_info:
        return jsonify({"error": "Task tidak ditemukan"}), 404
    return jsonify(task_info)

@app.route("/download-request/<task_id>")
def download_merged_file(task_id):
    task_info = session_tasks.get(task_id)
    if not task_info:
        return jsonify({"error": "Task tidak ditemukan"}), 404

    # Pastikan proses sudah selesai
    if task_info["status"] != "finished":
        return jsonify({"error": "Proses belum selesai"}), 400

    merged_dir = Path(archive_dir) / "requester" / task_id / "merged"
    if not merged_dir.exists():
        return jsonify({"error": "Folder hasil merge tidak ditemukan"}), 404

    # Cari file hasil merge
    merged_files = list(merged_dir.glob("merged_*.csv"))
    if not merged_files:
        return jsonify({"error": "File hasil merge belum tersedia"}), 404

    # Ambil file terbaru
    latest_file = max(merged_files, key=lambda f: f.stat().st_mtime)

    return send_file(
        str(latest_file),
        as_attachment=True,
        download_name=latest_file.name,
        mimetype="text/csv"
    )

@app.route("/cancel-request/<task_id>", methods=["POST"])
def cancel_request(task_id):
    task = session_tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task tidak ditemukan"}), 404

    task["cancelled"] = True
    task["status"] = "cancelled"
    return jsonify({"success": True})

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = env_int("APP_PORT", 5000)
    threads = env_int("APP_THREADS", 100)
    auto_update_project_reference()
    print(f"Server running on http://{host}:{port}")
    serve(app, host=host, port=port, threads=threads)
