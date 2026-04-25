"""
routes/thread_routes.py
Flask blueprint for the Threads feature.
"""
import os
import json
from flask import (
    Blueprint, render_template, request, jsonify,
    send_from_directory, abort, url_for
)
from services import thread_service as ts
from services import storage_service as ss

thread_bp = Blueprint("threads", __name__)

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREAD_IMAGES = os.path.join(BASE_DIR, "data", "threads", "images")


def _get_file(name: str):
    """Return the uploaded FileStorage or None when no file was selected."""
    f = request.files.get(name)
    return f if (f and f.filename) else None


def _parse_image_paths(path: str) -> list:
    """Parse image_path (JSON list or legacy single string) into a list of paths."""
    if not path:
        return []
    if path.startswith("["):
        try:
            return [p for p in json.loads(path) if p]
        except Exception:
            return [path]
    return [path]


def _image_urls(path: str) -> list:
    """Return a list of full URLs for the stored image_path value."""
    return [_image_url(p) for p in _parse_image_paths(path)]


# ── Static: serve thread images ───────────────────────────────────────────────
@thread_bp.route("/data/hub/images/<path:filename>")
def thread_image(filename):
    return send_from_directory(THREAD_IMAGES, filename)


# ── Pages ─────────────────────────────────────────────────────────────────────
@thread_bp.route("/hub")
def threads_page():
    ip   = _get_ip()
    user = ts.get_or_create_user(ip)
    if user and user.get("profile_pic"):
        user = dict(user)
        user["profile_pic"] = _image_url(user["profile_pic"])
    return render_template("threads.html", user=user, current_ip=ip)


@thread_bp.route("/post/<thread_id>")
def thread_detail_page(thread_id):
    ip     = _get_ip()
    user   = ts.get_or_create_user(ip)
    if user and user.get("profile_pic"):
        user = dict(user)
        user["profile_pic"] = _image_url(user["profile_pic"])
    thread = ts.get_thread(thread_id)
    if not thread:
        abort(404)
    # Resolve thread author's profile pic
    thread_author = ss.get_user(thread["user_ip"])
    thread["author_pic"]   = _image_url(thread_author.get("profile_pic", "")) if thread_author else ""
    thread["image_paths"]  = _parse_image_paths(thread.get("image_path", ""))
    comments = ts.list_comments(thread_id)
    # Resolve comment/reply author profile pics (one lookup per unique IP)
    _avatar_cache = {thread["user_ip"]: thread["author_pic"]}
    for c in comments:
        for obj in [c] + c.get("replies", []):
            tip = obj["user_ip"]
            if tip not in _avatar_cache:
                u = ss.get_user(tip)
                _avatar_cache[tip] = _image_url(u.get("profile_pic", "")) if u else ""
            obj["author_pic"] = _avatar_cache[tip]
    liked_ids = ts.get_user_liked_ids(ip)
    return render_template(
        "thread_detail.html",
        user=user,
        thread=thread,
        comments=comments,
        liked_ids=liked_ids,
        current_ip=ip,
    )


# ── API: Threads ──────────────────────────────────────────────────────────────
@thread_bp.route("/api/hub", methods=["GET"])
def api_get_threads():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    topic    = request.args.get("topic", "")
    ip       = _get_ip()
    result   = ts.list_threads(page, per_page, topic)
    liked_ids = ts.get_user_liked_ids(ip)
    # Batch-fetch comment counts for all threads in this page
    thread_ids = [t["thread_id"] for t in result["threads"]]
    comment_counts = ss.count_comments_for_threads(thread_ids)
    # Build per-IP avatar cache (one lookup per unique user in page)
    _avatar_cache = {}
    for t in result["threads"]:
        t_ip = t["user_ip"]
        if t_ip not in _avatar_cache:
            u = ss.get_user(t_ip)
            _avatar_cache[t_ip] = _image_url(u.get("profile_pic", "")) if u else ""
        t["liked"]         = t["thread_id"] in liked_ids
        t["share_url"]     = url_for("threads.thread_detail_page",
                                     thread_id=t["thread_id"], _external=True)
        t["image_urls"]    = _image_urls(t.get("image_path", ""))
        t["avatar_url"]    = _avatar_cache[t_ip]
        t["comment_count"] = comment_counts.get(t["thread_id"], 0)
    return jsonify(result)


@thread_bp.route("/api/hub", methods=["POST"])
def api_create_thread():
    ip   = _get_ip()
    user = ts.get_or_create_user(ip)
    if not user:
        return jsonify({"error": "Please set up your profile first."}), 401

    text  = (request.form.get("text") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    if not text:
        return jsonify({"error": "Text is required."}), 400

    image_files = [f for f in request.files.getlist("images") if f and f.filename][:20]
    thread, err = ts.create_thread(ip, user["username"], text, image_files, topic)
    if err:
        return jsonify({"error": err}), 400
    thread["share_url"]  = url_for("threads.thread_detail_page",
                                   thread_id=thread["thread_id"], _external=True)
    thread["image_urls"] = _image_urls(thread.get("image_path", ""))
    return jsonify(thread), 201


@thread_bp.route("/api/hub/<thread_id>", methods=["PUT"])
def api_update_thread(thread_id):
    ip   = _get_ip()
    text  = (request.form.get("text") or "").strip() or None
    topic = request.form.get("topic")
    image_files = [f for f in request.files.getlist("images") if f and f.filename][:20]
    ok, err = ts.edit_thread(thread_id, ip, text=text,
                              image_files=image_files, topic=topic)
    if not ok:
        return jsonify({"error": err or "Not found"}), 404
    thread = ts.get_thread(thread_id)
    thread["image_urls"] = _image_urls(thread.get("image_path", ""))
    return jsonify(thread)


@thread_bp.route("/api/hub/<thread_id>", methods=["DELETE"])
def api_delete_thread(thread_id):
    ip = _get_ip()
    ok = ts.remove_thread(thread_id, ip)
    if not ok:
        return jsonify({"error": "Not found or not authorized"}), 404
    return jsonify({"ok": True})


# ── API: Comments ─────────────────────────────────────────────────────────────
@thread_bp.route("/api/comment", methods=["POST"])
def api_create_comment():
    ip   = _get_ip()
    user = ts.get_or_create_user(ip)
    if not user:
        return jsonify({"error": "Please set up your profile first."}), 401

    thread_id         = (request.form.get("thread_id") or "").strip()
    text              = (request.form.get("text") or "").strip()
    parent_comment_id = (request.form.get("parent_comment_id") or "").strip()
    image_file        = _get_file("image")

    if not thread_id or not text:
        return jsonify({"error": "thread_id and text are required."}), 400

    comment, err = ts.add_comment(thread_id, ip, user["username"],
                                   text, parent_comment_id, image_file)
    if err:
        return jsonify({"error": err}), 400
    comment["image_url"] = _image_url(comment.get("image_path", ""))
    return jsonify(comment), 201


@thread_bp.route("/api/comment/<comment_id>/replies", methods=["GET"])
def api_get_replies(comment_id):
    ip = _get_ip()
    replies = ts.get_replies(comment_id)
    liked_ids = ts.get_user_liked_ids(ip)
    _avatar_cache = {}
    for r in replies:
        tip = r["user_ip"]
        if tip not in _avatar_cache:
            u = ss.get_user(tip)
            _avatar_cache[tip] = _image_url(u.get("profile_pic", "")) if u else ""
        r["liked"]      = r["comment_id"] in liked_ids
        r["image_url"]  = _image_url(r.get("image_path", ""))
        r["avatar_url"] = _avatar_cache[tip]
    return jsonify(replies)


@thread_bp.route("/api/comment/<comment_id>", methods=["PUT"])
def api_update_comment(comment_id):
    ip   = _get_ip()
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    ok = ts.edit_comment(comment_id, ip, text)
    if not ok:
        return jsonify({"error": "Not found or not authorized"}), 404
    return jsonify({"ok": True})


@thread_bp.route("/api/comment/<comment_id>", methods=["DELETE"])
def api_delete_comment(comment_id):
    ip = _get_ip()
    ok = ts.remove_comment(comment_id, ip)
    if not ok:
        return jsonify({"error": "Not found or not authorized"}), 404
    return jsonify({"ok": True})


# ── API: Likes ────────────────────────────────────────────────────────────────
@thread_bp.route("/api/like/thread", methods=["POST"])
def api_like_thread():
    ip        = _get_ip()
    user      = ts.get_or_create_user(ip)
    if not user:
        return jsonify({"error": "Please set up your profile first."}), 401
    data      = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id", "")
    if not thread_id:
        return jsonify({"error": "thread_id required"}), 400
    result = ts.toggle_like("thread", thread_id, ip)
    return jsonify(result)


@thread_bp.route("/api/like/comment", methods=["POST"])
def api_like_comment():
    ip         = _get_ip()
    user       = ts.get_or_create_user(ip)
    if not user:
        return jsonify({"error": "Please set up your profile first."}), 401
    data       = request.get_json(silent=True) or {}
    comment_id = data.get("comment_id", "")
    if not comment_id:
        return jsonify({"error": "comment_id required"}), 400
    result = ts.toggle_like("comment", comment_id, ip)
    return jsonify(result)


# ── API: Share ────────────────────────────────────────────────────────────────
@thread_bp.route("/api/share/thread", methods=["POST"])
def api_share_thread():
    data      = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id", "")
    if not thread_id:
        return jsonify({"error": "thread_id required"}), 400
    ts.share_thread(thread_id)
    share_url = url_for("threads.thread_detail_page",
                        thread_id=thread_id, _external=True)
    return jsonify({"share_url": share_url})


# ── API: User ─────────────────────────────────────────────────────────────────
@thread_bp.route("/api/user/me", methods=["GET"])
def api_get_me():
    ip   = _get_ip()
    user = ts.get_or_create_user(ip)
    if not user:
        return jsonify({"exists": False})
    return jsonify({
        "exists":      True,
        "username":    user["username"],
        "profile_pic": _image_url(user.get("profile_pic", "")),
        "created_at":  str(user.get("created_at", "")),
    })


@thread_bp.route("/api/user/update", methods=["POST"])
def api_update_user():
    ip           = _get_ip()
    username     = (request.form.get("username") or "").strip()
    profile_file = _get_file("profile_pic")

    if not username:
        return jsonify({"error": "username is required"}), 400
    if len(username) > 50:
        return jsonify({"error": "username too long (max 50 chars)"}), 400

    user, err = ts.upsert_user(ip, username, profile_file)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({
        "ok":          True,
        "username":    user["username"],
        "profile_pic": _image_url(user.get("profile_pic", "")) if isinstance(user, dict) else "",
    })


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_ip() -> str:
    """Return the client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _image_url(path: str) -> str:
    """Convert relative data path to a URL."""
    if not path:
        return ""
    # path stored as "threads/images/<filename>"
    filename = os.path.basename(path)
    return url_for("threads.thread_image", filename=filename)
