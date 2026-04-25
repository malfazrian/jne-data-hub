"""
thread_service.py
Business logic layer: image handling, validation, orchestration.
"""
import os
import uuid
import json
from typing import Optional, Tuple
from PIL import Image as PILImage

from services import storage_service as ss

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(BASE_DIR, "data", "threads", "images")
FILES_DIR  = os.path.join(BASE_DIR, "data", "threads", "files")
os.makedirs(FILES_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_FILE_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "ppt", "pptx",
    "zip", "rar", "7z", "tar", "gz", "json", "xml", "md", "rtf",
    "odt", "ods", "odp", "tsv", "log",
}
MAX_IMAGE_DIM  = 1200  # px – resize if larger
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per file


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_FILE_EXTENSIONS


def _safe_name(filename: str) -> str:
    """Sanitize original filename to a safe basename (no path traversal)."""
    import re
    name = os.path.basename(filename)
    # Replace anything that isn't alphanumeric, dash, underscore, or dot
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[:120] if name else "file"


def save_file(file_storage) -> Tuple[bool, object]:
    """
    Validate and save an uploaded file (non-image).
    Returns (True, {"path": rel_path, "name": original_name, "size": bytes})
         or (False, error_message).
    """
    if not file_storage or not file_storage.filename:
        return False, "No file provided"
    if not _allowed_file(file_storage.filename):
        ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
        return False, f"Tipe file '.{ext}' tidak diizinkan."

    ext      = file_storage.filename.rsplit(".", 1)[1].lower()
    safe_orig = _safe_name(file_storage.filename)
    filename  = f"{uuid.uuid4()}.{ext}"
    save_path = os.path.join(FILES_DIR, filename)

    try:
        file_storage.stream.seek(0)
        data = file_storage.stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return False, "Ukuran file melebihi batas 50 MB."
        with open(save_path, "wb") as fh:
            fh.write(data)
        file_size = len(data)
    except Exception as e:
        return False, f"Gagal menyimpan file: {e}"

    return True, {
        "path": f"threads/files/{filename}",
        "name": safe_orig,
        "size": file_size,
    }


def save_image(file_storage) -> Tuple[bool, str]:
    """
    Validate, resize, and save an uploaded image.
    Returns (True, relative_url_path) or (False, error_message).
    """
    if not file_storage or file_storage.filename == "":
        return False, "No file provided"
    if not _allowed(file_storage.filename):
        return False, "File type not allowed. Use JPG, PNG, GIF, or WEBP."

    ext      = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    save_path = os.path.join(IMAGES_DIR, filename)

    try:
        img = PILImage.open(file_storage.stream)
        img = img.convert("RGB") if ext not in ("png", "gif", "webp") else img
        # Resize if too large
        if max(img.width, img.height) > MAX_IMAGE_DIM:
            img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), PILImage.LANCZOS)
        img.save(save_path, optimize=True)
    except Exception as e:
        return False, f"Could not process image: {e}"

    # Return web-accessible relative path
    return True, f"threads/images/{filename}"


# ── Thread service ────────────────────────────────────────────────────────────

def create_thread(user_ip, username, text, image_files=None, file_files=None, topic=""):
    image_paths = []
    for f in (image_files or []):
        ok, result = save_image(f)
        if not ok:
            return None, result
        image_paths.append(result)
    image_path = json.dumps(image_paths) if image_paths else ""

    attached_files = []
    for f in (file_files or []):
        ok, result = save_file(f)
        if not ok:
            return None, result
        attached_files.append(result)
    file_path = json.dumps(attached_files) if attached_files else ""

    thread = ss.create_thread(user_ip, username, text, image_path, file_path, topic)
    return thread, None


def list_threads(page=1, per_page=20, topic=""):
    return ss.get_threads(page, per_page, topic)


def get_thread(thread_id):
    return ss.get_thread(thread_id)


def edit_thread(thread_id, user_ip, text=None, image_files=None, file_files=None, topic=None):
    image_path = None
    if image_files:
        paths = []
        for f in image_files:
            ok, result = save_image(f)
            if not ok:
                return False, result
            paths.append(result)
        image_path = json.dumps(paths)

    file_path = None
    if file_files:
        flist = []
        for f in file_files:
            ok, result = save_file(f)
            if not ok:
                return False, result
            flist.append(result)
        file_path = json.dumps(flist)

    ok = ss.update_thread(thread_id, user_ip, text=text, image_path=image_path,
                          file_path=file_path, topic=topic)
    return ok, None if ok else "Thread not found or not authorized"


def remove_thread(thread_id, user_ip):
    all_paths = ss.delete_thread(thread_id, user_ip)
    if all_paths is None:
        return False
    for rel_path in all_paths:
        if not rel_path:
            continue
        # Files stored under threads/files/ or threads/images/
        if "threads/files/" in rel_path:
            abs_path = os.path.join(FILES_DIR, os.path.basename(rel_path))
        else:
            abs_path = os.path.join(IMAGES_DIR, os.path.basename(rel_path))
        try:
            os.remove(abs_path)
        except OSError:
            pass
    return True


def share_thread(thread_id):
    return ss.increment_share(thread_id)


# ── Comment service ───────────────────────────────────────────────────────────

def add_comment(thread_id, user_ip, username, text,
                parent_comment_id="", image_file=None, file_file=None):
    image_path = ""
    if image_file:
        ok, result = save_image(image_file)
        if not ok:
            return None, result
        image_path = result

    file_path = ""
    if file_file:
        ok, result = save_file(file_file)
        if not ok:
            return None, result
        file_path = json.dumps([result])

    comment = ss.create_comment(thread_id, user_ip, username, text,
                                 parent_comment_id, image_path, file_path)
    return comment, None


def list_comments(thread_id):
    raw = ss.get_comments(thread_id)
    # Nest: build top-level and count replies (replies are lazy-loaded on demand)
    top_level = [c for c in raw if not c.get("parent_comment_id")]
    replies   = [c for c in raw if c.get("parent_comment_id")]
    for t in top_level:
        t["reply_count"] = sum(1 for r in replies if r["parent_comment_id"] == t["comment_id"])
    return top_level


def get_replies(comment_id):
    return ss.get_replies(comment_id)


def remove_comment(comment_id, user_ip):
    return ss.delete_comment(comment_id, user_ip)


def edit_comment(comment_id, user_ip, text):
    return ss.update_comment(comment_id, user_ip, text)


# ── Like service ──────────────────────────────────────────────────────────────

def toggle_like(kind, target_id, user_ip):
    return ss.toggle_like(kind, target_id, user_ip)


# ── User service ──────────────────────────────────────────────────────────────

def get_or_create_user(ip):
    return ss.get_user(ip)


def upsert_user(ip, username, profile_file=None):
    profile_path = None
    if profile_file:
        ok, result = save_image(profile_file)
        if not ok:
            return None, result
        profile_path = result
    user = ss.upsert_user(ip, username, profile_path)
    return user, None


def get_user_liked_ids(ip):
    return ss.get_user_likes(ip)
