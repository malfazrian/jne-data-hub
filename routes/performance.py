"""
Blueprint: Performance
Routes: /, /full, /report, /run, /run_full, /run_report,
        /download/<request_id>, /progress/<request_id>, /progress_status/<request_id>
"""
import os
import json
import time
import uuid
import shutil
import zipfile
import datetime
import threading
import traceback

from flask import (
    Blueprint, render_template, request, send_file,
    flash, redirect, url_for, Response, jsonify, current_app,
)

from scripts.ref_reader import load_projects, project_reference_mtime
from scripts.query_performance_custom import ProjectProcessor
from scripts.query_performance_parquet import ProjectProcessorParquet
from scripts.lists import criteria_lists, get_criteria_lists
from routes.shared import archive_dir

performance_bp = Blueprint("performance", __name__)

base_path         = os.getenv("CSV_BASE_PATH", "")
base_path_parquet = os.getenv("PARQUET_BASE_PATH", "")

# Shared progress dictionary for this blueprint
progress_status = {}  # {request_id: {"current": int, "total": int, "files": [...], "done": bool}}


# ── Helper functions ──────────────────────────────────────────────────────────

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
    """Check whether result files still exist; remove from dict if they don't."""
    status = progress_status.get(request_id)
    if not status:
        return None

    files = status.get("files", [])
    if status.get("done") and all(not os.path.exists(f) for f in files):
        progress_status.pop(request_id, None)
        current_app.logger.info(f"Progress {request_id} dihapus karena semua file sudah tidak ada.")
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
        print(f"[perf] Cleaned up expired request: {req_id}")


# ── Background thread: auto-cleanup ──────────────────────────────────────────

def _auto_cleanup_thread():
    while True:
        cleanup_old_results()
        time.sleep(3600)  # every 1 hour


threading.Thread(target=_auto_cleanup_thread, daemon=True).start()


# ── Core processing logic ─────────────────────────────────────────────────────

def start_processing(
    start, end, project_lists_json,
    full=False, report=False,
    project_status=None, criteria_lists=None, filter_categories=None,
    data_source="parquet", start_date=None, end_date=None,
):
    """Main processing function (runs background thread)."""
    clean_old_outputs(archive_dir, max_age_days=1)

    def yymm_to_date(val):
        if not val or len(val) != 4 or not val.isdigit():
            return None
        year  = int("20" + val[:2])
        month = int(val[2:])
        try:
            return year, month
        except Exception:
            return None

    def parse_ymd(val):
        if not val:
            return None
        try:
            return datetime.datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return None

    start_dt = parse_ymd(start_date)
    end_dt   = parse_ymd(end_date)

    if start_date or end_date:
        if not start_dt or not end_dt:
            return {"success": False, "message": "Format tanggal salah! Gunakan YYYY-MM-DD."}
        if start_dt > end_dt:
            return {"success": False, "message": "Tanggal awal tidak boleh lebih baru dari tanggal akhir!"}
        if not start or not end:
            start = start_dt.strftime("%y%m")
            end   = end_dt.strftime("%y%m")
    else:
        start_date_parsed = yymm_to_date(start)
        end_date_parsed   = yymm_to_date(end)
        if not start_date_parsed or not end_date_parsed:
            return {"success": False, "message": "Format periode salah! Gunakan YYMM."}
        if (start_date_parsed[0], start_date_parsed[1]) > (end_date_parsed[0], end_date_parsed[1]):
            return {"success": False, "message": "Bulan awal tidak boleh lebih baru dari bulan akhir!"}

    if not project_lists_json.strip():
        return {"success": False, "message": "Daftar project tidak boleh kosong!"}

    try:
        project_lists = json.loads(project_lists_json)
    except json.JSONDecodeError:
        return {"success": False, "message": "Format data project tidak valid!"}

    request_id       = str(uuid.uuid4())
    user_archive_dir = os.path.join(archive_dir, "master_data", request_id)
    os.makedirs(user_archive_dir, exist_ok=True)

    progress_status[request_id] = {
        "current": 0,
        "total":   1,
        "files":   [],
        "done":    False,
        "error":   None,
    }

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
                    end_date=end_dt,
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
                    end_date=end_dt,
                )

            saved_files = processor.run(
                request_id=request_id,
                filter_categories=filter_categories if full else None,
            )

            if not saved_files:
                shutil.rmtree(user_archive_dir, ignore_errors=True)
                progress_status[request_id] = {
                    "current": 100,
                    "total":   1,
                    "files":   [],
                    "done":    True,
                }
                return

            # Multiple projects → create ZIP now
            if len(saved_files) > 1:
                zip_path = os.path.join(user_archive_dir, "projects_performance.zip")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for fpath in saved_files:
                        if os.path.exists(fpath):
                            zipf.write(fpath, arcname=os.path.basename(fpath))
                progress_status[request_id]["files"] = [zip_path]
            else:
                progress_status[request_id]["files"] = saved_files

            progress_status[request_id].update({"current": 100, "done": True})

        except Exception as e:
            shutil.rmtree(user_archive_dir, ignore_errors=True)
            progress_status[request_id] = {
                "current": 1, "total": 1, "files": [], "done": True, "error": str(e),
            }
            print(f"❌ Error background job: {e}")
            print(traceback.format_exc())

    threading.Thread(target=background_job, daemon=True).start()

    mode_text = "FULL DATA" if full else "REPORT" if report else "PERFORMANCE"
    return {
        "success":    True,
        "message":    f"Proses {mode_text} sedang berjalan. Silakan tunggu...",
        "request_id": request_id,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@performance_bp.route("/", methods=["GET"])
def index():
    projects, project_names = load_projects()
    return render_template("index.html", projects_json=projects, project_names=project_names, mode="normal")


@performance_bp.route("/full", methods=["GET"])
def full_index():
    projects, project_names = load_projects()
    return render_template("index.html", projects_json=projects, project_names=project_names, mode="full")


@performance_bp.route("/report", methods=["GET"])
def report_index():
    projects, project_names = load_projects()
    return render_template("index.html", projects_json=projects, project_names=project_names, mode="report")


@performance_bp.route("/project_reference", methods=["GET"])
def project_reference():
    projects, project_names = load_projects()
    return jsonify({
        "projects": projects,
        "project_names": project_names,
        "mtime": project_reference_mtime(),
    })


@performance_bp.route("/run", methods=["POST"])
def run():
    data = request.get_json()
    return jsonify(start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        full=False,
        project_status=data.get("project_status"),
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    ))


@performance_bp.route("/run_full", methods=["POST"])
def run_full():
    data = request.get_json()
    return jsonify(start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        full=True,
        project_status=data.get("project_status"),
        filter_categories=data.get("filter_categories", []),
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    ))


@performance_bp.route("/run_report", methods=["POST"])
def run_report():
    data = request.get_json()
    return jsonify(start_processing(
        start=data.get("start"),
        end=data.get("end"),
        project_lists_json=data.get("project_lists_json", ""),
        report=True,
        project_status=data.get("project_status"),
        criteria_lists=get_criteria_lists(fallback=criteria_lists),
        data_source=data.get("data_source", "parquet"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    ))


@performance_bp.route("/download/<request_id>")
def download(request_id):
    status = progress_status.get(request_id)
    if not status or not status.get("done"):
        flash("File belum siap.", "danger")
        return redirect(url_for("performance.index"))

    files = status.get("files", [])
    if not files:
        flash("File tidak tersedia.", "danger")
        return redirect(url_for("performance.index"))

    return send_file(files[0], as_attachment=True)


@performance_bp.route("/progress/<request_id>")
def progress(request_id):
    def generate():
        last_sent = -1
        while True:
            status = progress_status.get(request_id)
            if not status:
                yield "data: 0\n\n"
                time.sleep(1)
                continue

            current = status.get("current", 0)
            total   = status.get("total", 1) or 1
            percent = int(current / total * 100)

            if percent != last_sent:
                yield f"data: {percent}\n\n"
                last_sent = percent

            if status.get("done", False):
                break

            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


@performance_bp.route("/progress_status/<request_id>")
def progress_status_api(request_id):
    status = validate_progress_status(request_id)
    if not status:
        return jsonify({
            "exists": False,
            "done":   False,
            "percent": 0,
            "error":  "File hasil sudah dihapus atau request tidak ditemukan.",
        }), 410

    current = status.get("current", 0)
    total   = status.get("total", 1) or 1
    percent = int(current / total * 100)

    return jsonify({
        "exists":  True,
        "done":    status.get("done", False),
        "percent": percent,
        "files":   status.get("files", []),
    })
