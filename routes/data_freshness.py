"""Reusable last-update status API for the application's primary datasets."""

import datetime
import os
import threading
import time

import psycopg2
from flask import Blueprint, current_app, jsonify


data_freshness_bp = Blueprint("data_freshness", __name__)

FRESH_HOURS = float(os.getenv("DATA_FRESHNESS_WARNING_HOURS", "24"))
STALE_HOURS = float(os.getenv("DATA_FRESHNESS_STALE_HOURS", "48"))
CACHE_TTL_SECONDS = int(os.getenv("DATA_FRESHNESS_CACHE_TTL_SECONDS", "300"))

SOURCE_LABELS = {
    "master": "Master Data DS",
    "pickup": "Pickup",
    "dwr": "DWR",
}

_freshness_cache = {}
_freshness_lock = threading.Lock()


def _file_updated_at(path_value):
    if not path_value:
        return None
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path_value))
    except (OSError, TypeError, ValueError):
        return None


def master_data_updated_at():
    return _file_updated_at(os.getenv("PARQUET_PROCESSED_FILE", ""))


def dwr_updated_at():
    return _file_updated_at(os.getenv("DWR_AWB_INDEX_PATH", ""))


def _pickup_db_config():
    return {
        "host": os.getenv("PICKUP_DB_HOST", "192.168.9.105"),
        "port": int(os.getenv("PICKUP_DB_PORT", "5432")),
        "dbname": os.getenv("PICKUP_DB_NAME", "pickup_db"),
        "user": os.getenv("PICKUP_DB_USER", "postgres"),
        "password": os.getenv("PICKUP_DB_PASSWORD", ""),
        "connect_timeout": 3,
    }


def pickup_updated_at():
    with psycopg2.connect(**_pickup_db_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'pickup'
                  AND table_name = 'cnote_daily'
                  AND column_name IN ('load_timestamp', 'report_date')
                """
            )
            available = {row[0] for row in cursor.fetchall()}
            column = "load_timestamp" if "load_timestamp" in available else (
                "report_date" if "report_date" in available else None
            )
            if column is None:
                return None
            cursor.execute(f"SELECT MAX({column}) FROM pickup.cnote_daily")
            row = cursor.fetchone()
            return row[0] if row else None


SOURCE_LOADERS = {
    "master": master_data_updated_at,
    "pickup": pickup_updated_at,
    "dwr": dwr_updated_at,
}


def classify_freshness(updated_at, now=None):
    if updated_at is None:
        return {"status": "unknown", "label": "Status tidak diketahui", "age_hours": None}

    if now is None:
        now = datetime.datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.datetime.now()
    age_hours = max(0.0, (now - updated_at).total_seconds() / 3600)

    if age_hours <= FRESH_HOURS:
        status, label = "fresh", "Terbarui"
    elif age_hours <= STALE_HOURS:
        status, label = "warning", "Perlu diperbarui"
    else:
        status, label = "stale", "Data lama"
    return {"status": status, "label": label, "age_hours": round(age_hours, 2)}


def _load_source_status(source):
    updated_at = SOURCE_LOADERS[source]()
    result = classify_freshness(updated_at)
    result.update({
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "updated_at": updated_at.isoformat() if updated_at else None,
    })
    return result


def get_source_status(source):
    now = time.time()
    with _freshness_lock:
        cached = _freshness_cache.get(source)
        if cached and now - cached[0] <= CACHE_TTL_SECONDS:
            return cached[1]

    try:
        result = _load_source_status(source)
    except Exception as exc:
        current_app.logger.warning("Unable to read %s freshness: %s", source, exc)
        result = {
            "source": source,
            "source_label": SOURCE_LABELS[source],
            "status": "unknown",
            "label": "Status tidak diketahui",
            "age_hours": None,
            "updated_at": None,
        }

    with _freshness_lock:
        _freshness_cache[source] = (now, result)
    return result


@data_freshness_bp.route("/data-freshness/<source>", methods=["GET"])
def data_freshness(source):
    if source not in SOURCE_LOADERS:
        return jsonify({"error": "Sumber data tidak dikenal."}), 404
    return jsonify(get_source_status(source))
