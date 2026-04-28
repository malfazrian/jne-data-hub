"""
Blueprint: APEX Requester
Routes: /apex_requester, /start-request, /progress-request/<task_id>,
        /download-request/<task_id>, /cancel-request/<task_id>
"""
import os
import datetime
import shutil
import time
from pathlib import Path
from threading import Thread

from flask import (
    Blueprint, render_template, request,
    send_file, jsonify,
)

from scripts.apex_query_by_id import ApexRequestProcessor
from routes.shared import archive_dir, APEX_DEFAULT_HOSTS

apex_requester_bp = Blueprint("apex_requester", __name__)

session_tasks = {}


# ── Helper functions ──────────────────────────────────────────────────────────

def cleanup_old_results():
    requester_dir = Path(archive_dir) / "requester"
    now = datetime.datetime.now()
    if requester_dir.exists():
        for name in os.listdir(requester_dir):
            path = requester_dir / name
            try:
                mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
                if (now - mtime).days >= 1:
                    shutil.rmtree(path, ignore_errors=True)
            except Exception as e:
                print(f"Error cleaning {path}: {e}")
    expired = [
        tid for tid in list(session_tasks)
        if not (requester_dir / tid).exists()
    ]
    for tid in expired:
        session_tasks.pop(tid, None)
        print(f"[requester] Cleaned up expired task: {tid}")


# ── Background thread: auto-cleanup ──────────────────────────────────────────

def _auto_cleanup_thread():
    while True:
        cleanup_old_results()
        time.sleep(3600)  # every 1 hour


Thread(target=_auto_cleanup_thread, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@apex_requester_bp.route("/apex_requester", methods=["GET"])
def apex_requester():
    return render_template("apex_requester.html")


@apex_requester_bp.route("/start-request", methods=["POST"])
def start_process():
    data         = request.json
    task_id      = str(__import__("uuid").uuid4())
    download_dir = Path(archive_dir) / "requester" / task_id
    os.makedirs(download_dir, exist_ok=True)

    host       = data.get("host")
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
        task_store=session_tasks,
    )

    session_tasks[task_id] = {
        "status":    "running",
        "progress":  0,
        "tracker":   processor.tracker.rows,
        "cancelled": False,
    }

    def run_job():
        def update_progress(pct, tracker_rows):
            session_tasks[task_id]["progress"] = pct
            session_tasks[task_id]["tracker"]  = tracker_rows

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
                session_tasks[task_id]["status"]   = "finished"
                session_tasks[task_id]["progress"]  = 100
                processor.merge_results()
                return
            else:
                print(f"Gagal di host {host}, mencoba host berikutnya...")

        if not processor.is_cancelled():
            session_tasks[task_id]["status"]  = "finished"
            session_tasks[task_id]["progress"] = 100

    Thread(target=run_job).start()
    return jsonify({"task_id": task_id})


@apex_requester_bp.route("/progress-request/<task_id>")
def get_progress(task_id):
    task_info = session_tasks.get(task_id)
    if not task_info:
        return jsonify({"error": "Task tidak ditemukan"}), 404
    return jsonify(task_info)


@apex_requester_bp.route("/download-request/<task_id>")
def download_merged_file(task_id):
    task_info = session_tasks.get(task_id)
    if not task_info:
        return jsonify({"error": "Task tidak ditemukan"}), 404

    if task_info["status"] != "finished":
        return jsonify({"error": "Proses belum selesai"}), 400

    merged_dir = Path(archive_dir) / "requester" / task_id / "merged"
    if not merged_dir.exists():
        return jsonify({"error": "Folder hasil merge tidak ditemukan"}), 404

    merged_files = list(merged_dir.glob("merged_*.csv"))
    if not merged_files:
        return jsonify({"error": "File hasil merge belum tersedia"}), 404

    latest_file = max(merged_files, key=lambda f: f.stat().st_mtime)
    return send_file(
        str(latest_file),
        as_attachment=True,
        download_name=latest_file.name,
        mimetype="text/csv",
    )


@apex_requester_bp.route("/cancel-request/<task_id>", methods=["POST"])
def cancel_request(task_id):
    task = session_tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task tidak ditemukan"}), 404

    task["cancelled"] = True
    task["status"]    = "cancelled"
    return jsonify({"success": True})
