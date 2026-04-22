"""
backup_syncer.py
----------------
Periodically copies today's report files from the network share to a local
backup directory so the report viewer can fall back to local copies when the
network is unavailable or returns 0 files.

Backup layout:
    D:\\RYAN\\Backup Trial Bot\\
        YYYY-MM-DD\\          <-- today's date folder (auto-deleted tomorrow)
            <file or sub-dir>
            ...

Old date folders are deleted automatically when the date rolls over.
"""

import os
import shutil
import datetime
import threading
import logging

logger = logging.getLogger(__name__)

BACKUP_ROOT = r"D:\RYAN\Backup Trial Bot"

_bulan = {
    1: "JANUARI",  2: "FEBRUARI", 3: "MARET",    4: "APRIL",
    5: "MEI",      6: "JUNI",     7: "JULI",      8: "AGUSTUS",
    9: "SEPTEMBER",10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER",
}

# ─── internal state ──────────────────────────────────────────────────────────
_lock = threading.Lock()
_last_sync_time: datetime.datetime | None = None
_last_sync_status: str = "not started"
_last_sync_file_count: int = 0
_using_backup: bool = False   # True when the viewer is serving from backup


def _build_network_report_path(date_obj: datetime.date, base: str) -> str:
    """Build the network 'trial BOT' path for a given date."""
    bulan_nama = _bulan[date_obj.month]
    tahun_2digit = str(date_obj.year)[-2:]
    bulan_num = f"{date_obj.month:02d}"
    tanggal_num = f"{date_obj.day:02d}"
    return os.path.join(
        base,
        "ALL REPORT GABUNGAN",
        f"{bulan_num}. {bulan_nama} {tahun_2digit}",
        f"{tanggal_num} {bulan_num} {tahun_2digit}",
        "trial BOT",
    )


def build_backup_dir(date_obj: datetime.date) -> str:
    """Return the local backup directory path for a given date."""
    return os.path.join(BACKUP_ROOT, date_obj.strftime("%Y-%m-%d"))


# ─── public helpers ───────────────────────────────────────────────────────────

def get_status() -> dict:
    """Return current sync status (for the admin API)."""
    with _lock:
        return {
            "last_sync": _last_sync_time.isoformat() if _last_sync_time else None,
            "status": _last_sync_status,
            "file_count": _last_sync_file_count,
            "backup_root": BACKUP_ROOT,
        }


def get_backup_file_list(date_obj: datetime.date) -> list[dict]:
    """
    Return a list of file-info dicts from the local backup for *date_obj*,
    in the same shape that app.py's /list_reports returns.
    Returns an empty list if the backup dir doesn't exist or is empty.
    """
    backup_dir = build_backup_dir(date_obj)
    if not os.path.isdir(backup_dir):
        return []

    files = []
    try:
        for entry in os.scandir(backup_dir):
            if entry.is_file():
                if entry.name.startswith("~$"):
                    continue
                mtime = entry.stat().st_mtime
                files.append({
                    "name": entry.name,
                    "size": entry.stat().st_size,
                    "modified": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_ts": mtime,
                    "tag": "UNKNOWN",
                    "rel_path": entry.name,
                    "from_backup": True,
                })
            elif entry.is_dir():
                tag = entry.name
                try:
                    for item in os.scandir(entry.path):
                        if not item.is_file() or item.name.startswith("~$"):
                            continue
                        mtime = item.stat().st_mtime
                        files.append({
                            "name": item.name,
                            "size": item.stat().st_size,
                            "modified": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "modified_ts": mtime,
                            "tag": tag,
                            "rel_path": f"{tag}/{item.name}",
                            "from_backup": True,
                        })
                except PermissionError:
                    pass
    except PermissionError:
        return []

    files.sort(key=lambda x: x["modified_ts"], reverse=True)
    return files


def resolve_backup_file(date_obj: datetime.date, rel_path: str) -> str | None:
    """
    Given a relative file path (e.g. "TAG/file.xlsx" or just "file.xlsx"),
    return the absolute local backup path if it exists, else None.
    Path-traversal is prevented.
    """
    backup_dir = os.path.realpath(build_backup_dir(date_obj))
    rel_norm = os.path.normpath(rel_path)
    if os.path.isabs(rel_norm):
        return None
    target = os.path.realpath(os.path.join(backup_dir, rel_norm))
    if not target.startswith(backup_dir + os.sep) and target != backup_dir:
        return None  # path-traversal attempt
    return target if os.path.isfile(target) else None


# ─── sync logic ───────────────────────────────────────────────────────────────

def _cleanup_old_backups(today: datetime.date) -> None:
    """Delete any backup date-folders that are not today."""
    if not os.path.isdir(BACKUP_ROOT):
        return
    today_str = today.strftime("%Y-%m-%d")
    for entry in os.scandir(BACKUP_ROOT):
        if entry.is_dir() and entry.name != today_str:
            try:
                shutil.rmtree(entry.path)
                logger.info(f"[backup_syncer] Deleted old backup: {entry.path}")
            except Exception as exc:
                logger.warning(f"[backup_syncer] Could not delete {entry.path}: {exc}")


def _sync_once(network_base: str) -> None:
    """Perform one sync: copy network files → local backup, then clean up."""
    global _last_sync_time, _last_sync_status, _last_sync_file_count

    today = datetime.date.today()
    network_path = _build_network_report_path(today, network_base)
    backup_dir = build_backup_dir(today)

    # Cleanup stale date folders first (date may have rolled over)
    _cleanup_old_backups(today)

    if not os.path.isdir(network_path):
        with _lock:
            _last_sync_time = datetime.datetime.now()
            _last_sync_status = f"network path not found: {network_path}"
            _last_sync_file_count = 0
        logger.warning(f"[backup_syncer] Network path not found: {network_path}")
        return

    try:
        os.makedirs(backup_dir, exist_ok=True)
        copied = 0

        for entry in os.scandir(network_path):
            if entry.is_file():
                if entry.name.startswith("~$"):
                    continue
                dst = os.path.join(backup_dir, entry.name)
                _copy_if_newer(entry.path, dst)
                copied += 1
            elif entry.is_dir():
                sub_dst = os.path.join(backup_dir, entry.name)
                os.makedirs(sub_dst, exist_ok=True)
                try:
                    for item in os.scandir(entry.path):
                        if not item.is_file() or item.name.startswith("~$"):
                            continue
                        dst = os.path.join(sub_dst, item.name)
                        _copy_if_newer(item.path, dst)
                        copied += 1
                except PermissionError as e:
                    logger.warning(f"[backup_syncer] Permission error in {entry.path}: {e}")

        with _lock:
            _last_sync_time = datetime.datetime.now()
            _last_sync_status = "ok"
            _last_sync_file_count = copied

        logger.info(f"[backup_syncer] Sync complete. {copied} file(s) backed up to {backup_dir}")

    except Exception as exc:
        with _lock:
            _last_sync_time = datetime.datetime.now()
            _last_sync_status = f"error: {exc}"
        logger.error(f"[backup_syncer] Sync failed: {exc}")


def _copy_if_newer(src: str, dst: str) -> None:
    """Copy src → dst only if src is newer than dst (or dst doesn't exist)."""
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
        return
    src_mtime = os.path.getmtime(src)
    dst_mtime = os.path.getmtime(dst)
    if src_mtime > dst_mtime:
        shutil.copy2(src, dst)


# ─── background thread ────────────────────────────────────────────────────────

def start_periodic_sync(network_base: str, interval_seconds: int = 300) -> None:
    """
    Start a daemon thread that syncs the report directory every
    *interval_seconds* seconds (default 5 minutes).
    Safe to call multiple times – only one thread is started.
    """
    if not network_base:
        logger.warning("[backup_syncer] REPORT_EXPLORER_BASE is empty – backup sync disabled.")
        return

    def _loop():
        logger.info(
            f"[backup_syncer] Periodic sync started "
            f"(interval={interval_seconds}s, root={BACKUP_ROOT})"
        )
        while True:
            try:
                _sync_once(network_base)
            except Exception as exc:
                logger.error(f"[backup_syncer] Unexpected error in sync loop: {exc}")
            threading.Event().wait(interval_seconds)

    t = threading.Thread(target=_loop, name="backup-syncer", daemon=True)
    t.start()
