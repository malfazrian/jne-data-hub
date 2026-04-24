"""
Shared utilities, environment variables, and state used across multiple blueprints.
"""
import os
import json
import datetime
import socket
import threading
import logging

from flask import request

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
archive_dir = os.path.join(BASE_DIR, "output")
os.makedirs(archive_dir, exist_ok=True)

USER_PREFS_FILE = os.path.join(BASE_DIR, "data", "user_preferences.json")
os.makedirs(os.path.dirname(USER_PREFS_FILE), exist_ok=True)

# ── Environment variables ─────────────────────────────────────────────────────
PROCESS_HISTORY_SOURCE = os.getenv("PROCESS_HISTORY_SOURCE", "")
PARQUET_PROCESSED_FILE = os.getenv("PARQUET_PROCESSED_FILE", "")
PARQUET_SOURCE_BASE    = os.getenv("PARQUET_SOURCE_BASE", "")
REPORT_EXPLORER_BASE   = os.getenv("REPORT_EXPLORER_BASE", "")
APEX_DEFAULT_HOSTS     = [h.strip() for h in os.getenv("APEX_DEFAULT_HOSTS", "").split(",") if h.strip()]

_BULAN = {
    1: "JANUARI",  2: "FEBRUARI", 3: "MARET",    4: "APRIL",
    5: "MEI",      6: "JUNI",     7: "JULI",     8: "AGUSTUS",
    9: "SEPTEMBER",10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER",
}

# ── User preferences ──────────────────────────────────────────────────────────
_prefs_lock = threading.Lock()


def _load_prefs_internal():
    """Internal: load preferences without locking (call only within locked context)."""
    if os.path.exists(USER_PREFS_FILE):
        try:
            with open(USER_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading preferences: {e}")
            return {}
    return {}


def _save_prefs_internal(data):
    """Internal: save preferences without locking (call only within locked context)."""
    temp_file = USER_PREFS_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if os.path.exists(USER_PREFS_FILE):
            os.replace(temp_file, USER_PREFS_FILE)
        else:
            os.rename(temp_file, USER_PREFS_FILE)
    except Exception as e:
        logger.error(f"Error saving preferences: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


def load_user_preferences():
    """Load user preferences from JSON file (thread-safe)."""
    with _prefs_lock:
        return _load_prefs_internal()


def save_user_preferences(data):
    """Save user preferences to JSON file (thread-safe)."""
    with _prefs_lock:
        _save_prefs_internal(data)


def extract_file_key(filename):
    """Extract key from filename (before dash)."""
    name_without_ext = os.path.splitext(filename)[0]
    if " - " in name_without_ext:
        return name_without_ext.split(" - ")[0].strip()
    return name_without_ext.strip()


def cleanup_old_recent_downloads(user_ip):
    """Remove recent downloads older than 1 day for a specific user."""
    with _prefs_lock:
        all_prefs = _load_prefs_internal()
        if user_ip not in all_prefs or "recent_downloads" not in all_prefs[user_ip]:
            return

        now = datetime.datetime.now()
        recent = all_prefs[user_ip]["recent_downloads"]

        filtered = []
        for item in recent:
            try:
                item_date = datetime.datetime.fromisoformat(item.get("timestamp", ""))
                if (
                    item_date.year == now.year
                    and item_date.month == now.month
                    and item_date.day == now.day
                ):
                    filtered.append(item)
            except (ValueError, TypeError):
                continue

        all_prefs[user_ip]["recent_downloads"] = filtered
        _save_prefs_internal(all_prefs)

        removed_count = len(recent) - len(filtered)
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old recent downloads for {user_ip}")


# ── Network / admin helpers ───────────────────────────────────────────────────

def get_client_ip():
    """Get client IP address."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0]
    return request.remote_addr or "unknown"


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
