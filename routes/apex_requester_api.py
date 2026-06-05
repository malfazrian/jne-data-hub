import os
import datetime
import shutil
import time
import uuid
from pathlib import Path
from threading import Thread

from flask import Blueprint, request, jsonify, send_file

from scripts.apex_query_by_id import ApexRequestProcessor
from routes.shared import archive_dir, APEX_DEFAULT_HOSTS

apex_requester_api_bp = Blueprint("apex_requester_api", __name__)

api_tasks = {}


def cleanup_old_api_results():
    requester_dir = Path(archive_dir) / "requester_api"
    now = datetime.datetime.now()

    if requester_dir.exists():
        for name in os.listdir(requester_dir):
            path = requester_dir / name
            try:
                mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
                if (now - mtime).days >= 1:
                    shutil.rmtree(path, ignore_errors=True)
            except Exception as e:
                print(f"[api requester] Error cleaning {path}: {e}")

    expired = [
        tid for tid in list(api_tasks)
        if not (requester_dir / tid).exists()
    ]

    for tid in expired:
        api_tasks.pop(tid, None)


def auto_cleanup_thread():
    while True:
        cleanup_old_api_results()
        time.sleep(3600)


Thread(target=auto_cleanup_thread, daemon=True).start()


@apex_requester_api_bp.route("/api/apex-request/start", methods=["POST"])
def api_start_request():
    data = request.get_json(silent=True) or {}

    required_fields = [
        "list_customer_ids",
        "tanggal_awal",
        "username",
        "password",
    ]

    missing = [field for field in required_fields if field not in data]
    if missing:
        return jsonify({
            "success": False,
            "error": f"Field wajib belum lengkap: {', '.join(missing)}"
        }), 400

    task_id = str(uuid.uuid4())
    download_dir = Path(archive_dir) / "requester_api" / task_id
    os.makedirs(download_dir, exist_ok=True)

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
        task_store=api_tasks,
    )

    api_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "tracker": processor.tracker.rows,
        "cancelled": False,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    def run_job():
        try:
            def update_progress(pct, tracker_rows):
                api_tasks[task_id]["progress"] = pct
                api_tasks[task_id]["tracker"] = tracker_rows

            processor.progress_callback = update_progress

            for host in processor.apex_hosts:
                if processor.is_cancelled():
                    api_tasks[task_id]["status"] = "cancelled"
                    return

                success = processor._connect_and_request(host)

                if processor.is_cancelled():
                    api_tasks[task_id]["status"] = "cancelled"
                    return

                if success:
                    processor.merge_results()
                    api_tasks[task_id]["status"] = "finished"
                    api_tasks[task_id]["progress"] = 100
                    return

            api_tasks[task_id]["status"] = "finished"
            api_tasks[task_id]["progress"] = 100

        except Exception as e:
            api_tasks[task_id]["status"] = "error"
            api_tasks[task_id]["error"] = str(e)

    Thread(target=run_job, daemon=True).start()

    return jsonify({
        "success": True,
        "task_id": task_id,
        "progress_url": f"/api/apex-request/progress/{task_id}",
        "download_url": f"/api/apex-request/download/{task_id}",
        "cancel_url": f"/api/apex-request/cancel/{task_id}",
    })


@apex_requester_api_bp.route("/api/apex-request/progress/<task_id>", methods=["GET"])
def api_progress_request(task_id):
    task = api_tasks.get(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": "Task tidak ditemukan"
        }), 404

    return jsonify({
        "success": True,
        "task": task
    })


@apex_requester_api_bp.route("/api/apex-request/download/<task_id>", methods=["GET"])
def api_download_request(task_id):
    task = api_tasks.get(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": "Task tidak ditemukan"
        }), 404

    if task["status"] != "finished":
        return jsonify({
            "success": False,
            "error": "Proses belum selesai"
        }), 400

    merged_dir = Path(archive_dir) / "requester_api" / task_id / "merged"

    if not merged_dir.exists():
        return jsonify({
            "success": False,
            "error": "Folder hasil merge tidak ditemukan"
        }), 404

    merged_files = list(merged_dir.glob("merged_*.csv"))

    if not merged_files:
        return jsonify({
            "success": False,
            "error": "File hasil merge belum tersedia"
        }), 404

    latest_file = max(merged_files, key=lambda f: f.stat().st_mtime)

    return send_file(
        str(latest_file),
        as_attachment=True,
        download_name=latest_file.name,
        mimetype="text/csv",
    )


@apex_requester_api_bp.route("/api/apex-request/cancel/<task_id>", methods=["POST"])
def api_cancel_request(task_id):
    task = api_tasks.get(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": "Task tidak ditemukan"
        }), 404

    task["cancelled"] = True
    task["status"] = "cancelled"

    return jsonify({
        "success": True,
        "message": "Task dibatalkan"
    })