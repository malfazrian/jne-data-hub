"""
thread_service.py
Business logic layer: image handling, validation, orchestration.
"""
import os
import uuid
from typing import Optional, Tuple
from PIL import Image as PILImage

from services import storage_service as ss

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(BASE_DIR, "data", "threads", "images")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_IMAGE_DIM = 1200  # px – resize if larger


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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

def create_thread(user_ip, username, text, image_file=None, topic=""):
    image_path = ""
    if image_file:
        ok, result = save_image(image_file)
        if not ok:
            return None, result
        image_path = result
    thread = ss.create_thread(user_ip, username, text, image_path, topic)
    return thread, None


def list_threads(page=1, per_page=20, topic=""):
    return ss.get_threads(page, per_page, topic)


def get_thread(thread_id):
    return ss.get_thread(thread_id)


def edit_thread(thread_id, user_ip, text=None, image_file=None, topic=None):
    image_path = None
    if image_file:
        ok, result = save_image(image_file)
        if not ok:
            return False, result
        image_path = result
    ok = ss.update_thread(thread_id, user_ip, text=text, image_path=image_path, topic=topic)
    return ok, None if ok else "Thread not found or not authorized"


def remove_thread(thread_id, user_ip):
    return ss.delete_thread(thread_id, user_ip)


def share_thread(thread_id):
    return ss.increment_share(thread_id)


# ── Comment service ───────────────────────────────────────────────────────────

def add_comment(thread_id, user_ip, username, text,
                parent_comment_id="", image_file=None):
    image_path = ""
    if image_file:
        ok, result = save_image(image_file)
        if not ok:
            return None, result
        image_path = result
    comment = ss.create_comment(thread_id, user_ip, username, text,
                                 parent_comment_id, image_path)
    return comment, None


def list_comments(thread_id):
    raw = ss.get_comments(thread_id)
    # Nest: build top-level and replies
    top_level = [c for c in raw if not c.get("parent_comment_id")]
    replies   = [c for c in raw if c.get("parent_comment_id")]
    for t in top_level:
        t["replies"] = [r for r in replies if r["parent_comment_id"] == t["comment_id"]]
    return top_level


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
