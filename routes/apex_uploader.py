"""
Blueprint: APEX Uploader
Routes: /apex_uploader, /start_job, /download-apex/<job_id>,
        /progress-apex/<job_id>, /check-job/<job_id>, /cancel-job/<job_id>
"""
import json
import os
import time
import uuid
import datetime
import shutil
from pathlib import Path
from threading import Thread

from flask import (
    Blueprint, render_template, request,
    send_file, jsonify, Response, stream_with_context,
)

from scripts.apex_query import ApexQueryJob
from routes.shared import archive_dir

apex_uploader_bp = Blueprint("apex_uploader", __name__)

job_status = {}


# ── Helper functions ──────────────────────────────────────────────────────────

def cleanup_old_results():
    uploader_dir = Path(archive_dir) / "uploader"
    now = datetime.datetime.now()
    if uploader_dir.exists():
        for name in os.listdir(uploader_dir):
            path = uploader_dir / name
            try:
                mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
                if (now - mtime).days >= 1:
                    shutil.rmtree(path, ignore_errors=True)
            except Exception as e:
                print(f"Error cleaning {path}: {e}")
    expired = [
        jid for jid in list(job_status)
        if not (uploader_dir / jid).exists()
    ]
    for jid in expired:
        job_status.pop(jid, None)
        print(f"[uploader] Cleaned up expired job: {jid}")


# ── Background thread: auto-cleanup ──────────────────────────────────────────

def _auto_cleanup_thread():
    while True:
        cleanup_old_results()
        time.sleep(3600)  # every 1 hour


Thread(target=_auto_cleanup_thread, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@apex_uploader_bp.route("/apex_uploader", methods=["GET"])
def apex_uploader():
    return render_template("apex_uploader.html")


@apex_uploader_bp.route("/start_job", methods=["POST"])
def start_job():
    username = request.form.get("username")
    password = request.form.get("password")
    host     = request.form.get("host")
    files    = request.files.getlist("source_files")

    try:
        chunk_size = int(request.form.get("chunk", 9999))
        if chunk_size <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Nilai chunk harus berupa angka positif."}), 400

    if not username or not password or len(files) == 0 or not host:
        return jsonify({"error": "Semua field wajib diisi."}), 400

    job_id     = str(uuid.uuid4())
    job_dir    = Path(archive_dir) / "uploader" / job_id
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

    job_status[job_id] = {"progress": 0, "log": [], "done": False, "cancelled": False}

    def run_job():
        job = ApexQueryJob(
            base_dir=str(job_dir),
            username=username,
            password=password,
            request_id=job_id,
            status_dict=job_status,
        )
        job.selected_host = host
        job.chunk_size    = chunk_size
        result = job.run()
        job_status[job_id]["log"].append(result.get("message", "Selesai."))
        job_status[job_id]["done"]     = True
        job_status[job_id]["progress"] = 100

    Thread(target=run_job, daemon=True).start()
    return jsonify({"job_id": job_id})


@apex_uploader_bp.route("/download-apex/<job_id>")
def download_result(job_id):
    job_dir     = Path(archive_dir) / "uploader" / job_id
    result_file = job_dir / "downloads" / "Updated AWB.csv"

    if not result_file.exists():
        print("🔍 Folder contents:", list(job_dir.glob("*")))
        return jsonify({"error": "File hasil tidak ditemukan."}), 404

    return send_file(result_file, as_attachment=True)


@apex_uploader_bp.route("/progress-apex/<job_id>")
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
                "log":      status.get("log", []),
                "done":     status.get("done", False),
            }
            msg = json.dumps(data)
            if msg != last_state:
                yield f"data: {msg}\n\n"
                last_state = msg

            if status.get("done", False):
                break

            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


@apex_uploader_bp.route("/check-job/<job_id>")
def check_job(job_id):
    job = job_status.get(job_id)
    if not job:
        return jsonify({"exists": False})
    return jsonify({"exists": True, "done": job.get("done", False)})


@apex_uploader_bp.route("/cancel-job/<job_id>", methods=["POST"])
def cancel_job(job_id):
    job = job_status.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    job["cancelled"] = True
    job["log"].append("⛔ Job dibatalkan oleh user.")
    return jsonify({"success": True})
