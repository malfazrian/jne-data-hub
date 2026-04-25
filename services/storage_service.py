"""
storage_service.py
DuckDB + Parquet storage layer for the Threads feature.
All queries go directly to parquet files without loading full data into memory.
"""
import os
import uuid
import datetime
import threading
from typing import Optional

import duckdb

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREADS_DIR  = os.path.join(BASE_DIR, "data", "threads")
IMAGES_DIR   = os.path.join(THREADS_DIR, "images")
USERS_FILE   = os.path.join(THREADS_DIR, "users.parquet")

os.makedirs(THREADS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR,  exist_ok=True)


# ── Partition helpers ─────────────────────────────────────────────────────────
def _quarter(dt: datetime.datetime) -> str:
    """Return 'Q1'/'Q2'/'Q3'/'Q4' for a given datetime."""
    return f"Q{(dt.month - 1) // 3 + 1}"


def _parquet_path(kind: str, dt: datetime.datetime) -> str:
    """Return parquet file path for threads/comments partitioned by quarter."""
    year = dt.year
    q    = _quarter(dt)
    return os.path.join(THREADS_DIR, f"{kind}_{year}_{q}.parquet")


def _glob_path(kind: str) -> str:
    """Glob pattern to read all partitions for a kind."""
    return os.path.join(THREADS_DIR, f"{kind}_*.parquet").replace("\\", "/")


# ── DuckDB connection (read/write) ────────────────────────────────────────────
def _conn():
    return duckdb.connect()


# ── Per-file write locks (prevents concurrent writes to same parquet file) ────
_file_locks: dict = {}
_file_locks_mutex = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    with _file_locks_mutex:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


def _tmp(path: str) -> str:
    """Generate a unique temp file path next to the target file."""
    return os.path.join(
        os.path.dirname(path),
        f"_tmp_{uuid.uuid4().hex}.parquet"
    )


# ── Schema initializers ───────────────────────────────────────────────────────
def ensure_users_parquet():
    if not os.path.exists(USERS_FILE):
        with _get_lock(USERS_FILE):
            if not os.path.exists(USERS_FILE):   # double-check after acquiring lock
                con = _conn()
                try:
                    con.execute("""
                        CREATE TABLE users (
                            ip_address   VARCHAR,
                            username     VARCHAR,
                            profile_pic  VARCHAR,
                            created_at   TIMESTAMP
                        )
                    """)
                    con.execute(f"COPY users TO '{_esc(USERS_FILE)}' (FORMAT PARQUET)")
                finally:
                    con.close()


def _thread_cols():
    return """
        thread_id         VARCHAR,
        user_ip           VARCHAR,
        username_snapshot VARCHAR,
        text              VARCHAR,
        image_path        VARCHAR,
        topic             VARCHAR,
        created_at        TIMESTAMP,
        like_count        BIGINT,
        share_count       BIGINT
    """


def _comment_cols():
    return """
        comment_id        VARCHAR,
        thread_id         VARCHAR,
        parent_comment_id VARCHAR,
        user_ip           VARCHAR,
        username_snapshot VARCHAR,
        text              VARCHAR,
        image_path        VARCHAR,
        created_at        TIMESTAMP,
        like_count        BIGINT
    """


def _likes_cols():
    return """
        like_id    VARCHAR,
        kind       VARCHAR,
        target_id  VARCHAR,
        user_ip    VARCHAR,
        created_at TIMESTAMP
    """


def _esc(path: str) -> str:
    """Escape backslashes for DuckDB SQL strings."""
    return path.replace("\\", "/")


def _ensure_partition(kind: str, dt: datetime.datetime):
    """Create parquet partition file if it doesn't exist."""
    path = _parquet_path(kind, dt)
    if not os.path.exists(path):
        with _get_lock(path):
            if not os.path.exists(path):
                con = _conn()
                try:
                    if kind == "threads":
                        con.execute(f"CREATE TABLE t ({_thread_cols()})")
                    elif kind == "comments":
                        con.execute(f"CREATE TABLE t ({_comment_cols()})")
                    elif kind == "likes":
                        con.execute(f"CREATE TABLE t ({_likes_cols()})")
                    con.execute(f"COPY t TO '{_esc(path)}' (FORMAT PARQUET)")
                finally:
                    con.close()


def _append_row(kind: str, dt: datetime.datetime, row: dict):
    """Append a single row to the appropriate partition parquet file."""
    _ensure_partition(kind, dt)
    path = _parquet_path(kind, dt)

    cols         = ", ".join(row.keys())
    params       = list(row.values())
    placeholders = ", ".join(["?" for _ in params])

    with _get_lock(path):
        tmp = _tmp(path)
        con = _conn()
        try:
            con.execute(f"CREATE TABLE existing AS SELECT * FROM read_parquet('{_esc(path)}')")
            con.execute(f"INSERT INTO existing ({cols}) VALUES ({placeholders})", params)
            con.execute(f"COPY existing TO '{_esc(tmp)}' (FORMAT PARQUET)")
        finally:
            con.close()
        os.replace(tmp, path)


def _rewrite_partition(kind: str, dt: datetime.datetime, sql_filter: str,
                       updates: Optional[dict] = None):
    """
    Rewrite a partition applying either a delete (updates=None keeps rows not matching filter)
    or an update (set columns in updates where filter matches).
    sql_filter: WHERE clause fragment, e.g. "thread_id = 'xxx'"
    """
    path = _parquet_path(kind, dt)
    if not os.path.exists(path):
        return

    with _get_lock(path):
        tmp = _tmp(path)
        con = _conn()
        try:
            if updates is None:
                # DELETE: keep rows that do NOT match filter
                con.execute(
                    f"CREATE TABLE t AS SELECT * FROM read_parquet('{_esc(path)}') "
                    f"WHERE NOT ({sql_filter})"
                )
            else:
                # UPDATE
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                vals = list(updates.values())
                con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{_esc(path)}')")
                con.execute(f"UPDATE t SET {set_clause} WHERE {sql_filter}", vals)
            con.execute(f"COPY t TO '{_esc(tmp)}' (FORMAT PARQUET)")
        finally:
            con.close()
        os.replace(tmp, path)


# ── Partition list from date range ───────────────────────────────────────────
def _partitions_for_range(kind: str, start: datetime.datetime, end: datetime.datetime):
    """Return list of existing partition paths that overlap with [start, end]."""
    paths = []
    cur = datetime.datetime(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while cur <= end:
        p = _parquet_path(kind, cur)
        if os.path.exists(p):
            paths.append(p)
        # advance one quarter
        month = cur.month + 3
        year  = cur.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        cur   = datetime.datetime(year, month, 1)
    return paths


def _all_existing_partitions(kind: str):
    import glob
    return glob.glob(os.path.join(THREADS_DIR, f"{kind}_*.parquet"))


# ══════════════════════════════════════════════════════════════════════════════
# USER OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_user(ip: str) -> Optional[dict]:
    ensure_users_parquet()
    if not os.path.exists(USERS_FILE):
        return None
    con = _conn()
    try:
        rows = con.execute(
            f"SELECT * FROM read_parquet('{_esc(USERS_FILE)}') WHERE ip_address = ? LIMIT 1",
            [ip]
        ).fetchdf()
        if rows.empty:
            return None
        row = rows.iloc[0].to_dict()
        row["created_at"] = str(row["created_at"])
        return row
    finally:
        con.close()


def upsert_user(ip: str, username: str, profile_pic: Optional[str] = None) -> dict:
    ensure_users_parquet()
    existing = get_user(ip)
    now = datetime.datetime.utcnow()

    with _get_lock(USERS_FILE):
        if existing:
            updates = {"username": username}
            if profile_pic is not None:
                updates["profile_pic"] = profile_pic
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            vals = list(updates.values())
            tmp = _tmp(USERS_FILE)
            con = _conn()
            try:
                con.execute(f"CREATE TABLE u AS SELECT * FROM read_parquet('{_esc(USERS_FILE)}')")
                con.execute(f"UPDATE u SET {set_clause} WHERE ip_address = ?", vals + [ip])
                con.execute(f"COPY u TO '{_esc(tmp)}' (FORMAT PARQUET)")
            finally:
                con.close()
            os.replace(tmp, USERS_FILE)
        else:
            tmp = _tmp(USERS_FILE)
            con = _conn()
            try:
                con.execute(f"CREATE TABLE u AS SELECT * FROM read_parquet('{_esc(USERS_FILE)}')")
                con.execute(
                    "INSERT INTO u VALUES (?, ?, ?, ?)",
                    [ip, username, profile_pic or "", now]
                )
                con.execute(f"COPY u TO '{_esc(tmp)}' (FORMAT PARQUET)")
            finally:
                con.close()
            os.replace(tmp, USERS_FILE)

    return get_user(ip)


# ══════════════════════════════════════════════════════════════════════════════
# THREAD OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def create_thread(user_ip: str, username: str, text: str,
                  image_path: str = "", topic: str = "") -> dict:
    now = datetime.datetime.utcnow()
    row = {
        "thread_id":         str(uuid.uuid4()),
        "user_ip":           user_ip,
        "username_snapshot": username,
        "text":              text,
        "image_path":        image_path,
        "topic":             topic,
        "created_at":        now,
        "like_count":        0,
        "share_count":       0,
    }
    _append_row("threads", now, row)
    row["created_at"] = str(now)
    return row


def get_threads(page: int = 1, per_page: int = 20, topic: str = "") -> dict:
    """Return paginated threads, newest first."""
    partitions = _all_existing_partitions("threads")
    if not partitions:
        return {"threads": [], "total": 0, "page": page, "per_page": per_page}

    glob_str = "', '".join([_esc(p) for p in partitions])
    topic_filter = f"AND topic = '{topic}'" if topic else ""

    con = _conn()
    try:
        total = con.execute(
            f"SELECT COUNT(*) FROM read_parquet(['{glob_str}']) WHERE 1=1 {topic_filter}"
        ).fetchone()[0]

        offset = (page - 1) * per_page
        rows = con.execute(
            f"""
            SELECT * FROM read_parquet(['{glob_str}'])
            WHERE 1=1 {topic_filter}
            ORDER BY created_at DESC
            LIMIT {per_page} OFFSET {offset}
            """
        ).fetchdf()

        threads = rows.to_dict(orient="records")
        for t in threads:
            t["created_at"] = str(t["created_at"])
        return {"threads": threads, "total": total, "page": page, "per_page": per_page}
    finally:
        con.close()


def get_thread(thread_id: str) -> Optional[dict]:
    partitions = _all_existing_partitions("threads")
    if not partitions:
        return None
    glob_str = "', '".join([_esc(p) for p in partitions])
    con = _conn()
    try:
        rows = con.execute(
            f"SELECT * FROM read_parquet(['{glob_str}']) WHERE thread_id = ? LIMIT 1",
            [thread_id]
        ).fetchdf()
        if rows.empty:
            return None
        row = rows.iloc[0].to_dict()
        row["created_at"] = str(row["created_at"])
        return row
    finally:
        con.close()


def _get_thread_created_at(thread_id: str) -> Optional[datetime.datetime]:
    t = get_thread(thread_id)
    if not t:
        return None
    return datetime.datetime.fromisoformat(t["created_at"].replace("Z", ""))


def update_thread(thread_id: str, user_ip: str, text: str = None,
                  image_path: str = None, topic: str = None) -> bool:
    dt = _get_thread_created_at(thread_id)
    if not dt:
        return False
    updates = {}
    if text       is not None: updates["text"]       = text
    if image_path is not None: updates["image_path"] = image_path
    if topic      is not None: updates["topic"]      = topic
    if not updates:
        return False
    _rewrite_partition("threads", dt,
                       f"thread_id = '{thread_id}' AND user_ip = '{user_ip}'",
                       updates)
    return True


def delete_thread(thread_id: str, user_ip: str) -> bool:
    dt = _get_thread_created_at(thread_id)
    if not dt:
        return False
    _rewrite_partition("threads", dt,
                       f"thread_id = '{thread_id}' AND user_ip = '{user_ip}'")
    # also delete comments
    for p in _all_existing_partitions("comments"):
        # parse dt from filename
        fname = os.path.basename(p).replace("comments_", "").replace(".parquet", "")
        parts = fname.split("_")
        year  = int(parts[0])
        q     = int(parts[1][1])
        cdt   = datetime.datetime(year, (q - 1) * 3 + 1, 1)
        _rewrite_partition("comments", cdt, f"thread_id = '{thread_id}'")
    return True


def increment_share(thread_id: str) -> bool:
    dt = _get_thread_created_at(thread_id)
    if not dt:
        return False
    path = _parquet_path("threads", dt)
    with _get_lock(path):
        tmp = _tmp(path)
        con = _conn()
        try:
            con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{_esc(path)}')")
            con.execute("UPDATE t SET share_count = share_count + 1 WHERE thread_id = ?", [thread_id])
            con.execute(f"COPY t TO '{_esc(tmp)}' (FORMAT PARQUET)")
        finally:
            con.close()
        os.replace(tmp, path)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# COMMENT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def create_comment(thread_id: str, user_ip: str, username: str, text: str,
                   parent_comment_id: str = "", image_path: str = "") -> dict:
    now = datetime.datetime.utcnow()
    row = {
        "comment_id":        str(uuid.uuid4()),
        "thread_id":         thread_id,
        "parent_comment_id": parent_comment_id,
        "user_ip":           user_ip,
        "username_snapshot": username,
        "text":              text,
        "image_path":        image_path,
        "created_at":        now,
        "like_count":        0,
    }
    _append_row("comments", now, row)
    row["created_at"] = str(now)
    return row


def get_comments(thread_id: str) -> list:
    partitions = _all_existing_partitions("comments")
    if not partitions:
        return []
    glob_str = "', '".join([_esc(p) for p in partitions])
    con = _conn()
    try:
        rows = con.execute(
            f"""
            SELECT * FROM read_parquet(['{glob_str}'])
            WHERE thread_id = ?
            ORDER BY created_at ASC
            """,
            [thread_id]
        ).fetchdf()
        if rows.empty:
            return []
        result = rows.to_dict(orient="records")
        for r in result:
            r["created_at"] = str(r["created_at"])
        return result
    finally:
        con.close()


def count_comments_for_threads(thread_ids: list) -> dict:
    """Return {thread_id: top-level comment count} for a list of thread IDs."""
    if not thread_ids:
        return {}
    partitions = _all_existing_partitions("comments")
    if not partitions:
        return {tid: 0 for tid in thread_ids}
    glob_str = "', '".join([_esc(p) for p in partitions])
    ids_sql  = ", ".join([f"'{tid}'" for tid in thread_ids])
    con = _conn()
    try:
        rows = con.execute(
            f"""
            SELECT thread_id, COUNT(*) AS cnt
            FROM read_parquet(['{glob_str}'])
            WHERE thread_id IN ({ids_sql})
              AND (parent_comment_id IS NULL OR parent_comment_id = '')
            GROUP BY thread_id
            """
        ).fetchdf()
    finally:
        con.close()
    result = {tid: 0 for tid in thread_ids}
    for _, row in rows.iterrows():
        result[row["thread_id"]] = int(row["cnt"])
    return result


def get_replies(comment_id: str) -> list:
    """Return all replies (children) for a given top-level comment, oldest first."""
    partitions = _all_existing_partitions("comments")
    if not partitions:
        return []
    glob_str = "', '".join([_esc(p) for p in partitions])
    con = _conn()
    try:
        rows = con.execute(
            f"""
            SELECT * FROM read_parquet(['{glob_str}'])
            WHERE parent_comment_id = ?
            ORDER BY created_at ASC
            """,
            [comment_id]
        ).fetchdf()
        if rows.empty:
            return []
        result = rows.to_dict(orient="records")
        for r in result:
            r["created_at"] = str(r["created_at"])
        return result
    finally:
        con.close()


def delete_comment(comment_id: str, user_ip: str) -> bool:
    for p in _all_existing_partitions("comments"):
        fname = os.path.basename(p).replace("comments_", "").replace(".parquet", "")
        parts = fname.split("_")
        year  = int(parts[0])
        q     = int(parts[1][1])
        cdt   = datetime.datetime(year, (q - 1) * 3 + 1, 1)
        con = _conn()
        exists = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_esc(p)}') WHERE comment_id = ? AND user_ip = ?",
            [comment_id, user_ip]
        ).fetchone()[0]
        con.close()
        if exists:
            _rewrite_partition("comments", cdt,
                               f"comment_id = '{comment_id}' AND user_ip = '{user_ip}'")
            return True
    return False


def update_comment(comment_id: str, user_ip: str, text: str) -> bool:
    for p in _all_existing_partitions("comments"):
        fname = os.path.basename(p).replace("comments_", "").replace(".parquet", "")
        parts = fname.split("_")
        year  = int(parts[0])
        q     = int(parts[1][1])
        cdt   = datetime.datetime(year, (q - 1) * 3 + 1, 1)
        con = _conn()
        exists = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_esc(p)}') WHERE comment_id = ? AND user_ip = ?",
            [comment_id, user_ip]
        ).fetchone()[0]
        con.close()
        if exists:
            _rewrite_partition("comments", cdt,
                               f"comment_id = '{comment_id}' AND user_ip = '{user_ip}'",
                               {"text": text})
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# LIKE OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

LIKES_FILE = os.path.join(THREADS_DIR, "likes.parquet")


def _ensure_likes_parquet():
    if not os.path.exists(LIKES_FILE):
        with _get_lock(LIKES_FILE):
            if not os.path.exists(LIKES_FILE):
                con = _conn()
                try:
                    con.execute(f"CREATE TABLE l ({_likes_cols()})")
                    con.execute(f"COPY l TO '{_esc(LIKES_FILE)}' (FORMAT PARQUET)")
                finally:
                    con.close()


def toggle_like(kind: str, target_id: str, user_ip: str) -> dict:
    """Toggle like. kind='thread' or 'comment'. Returns {'liked': bool, 'count': int}."""
    _ensure_likes_parquet()

    with _get_lock(LIKES_FILE):
        con = _conn()
        try:
            existing = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{_esc(LIKES_FILE)}') "
                f"WHERE kind=? AND target_id=? AND user_ip=?",
                [kind, target_id, user_ip]
            ).fetchone()[0]
        finally:
            con.close()

        tmp = _tmp(LIKES_FILE)
        con = _conn()
        try:
            con.execute(f"CREATE TABLE l AS SELECT * FROM read_parquet('{_esc(LIKES_FILE)}')")
            if existing:
                con.execute(
                    "DELETE FROM l WHERE kind=? AND target_id=? AND user_ip=?",
                    [kind, target_id, user_ip]
                )
                liked = False
            else:
                con.execute(
                    "INSERT INTO l VALUES (?, ?, ?, ?, ?)",
                    [str(uuid.uuid4()), kind, target_id, user_ip, datetime.datetime.utcnow()]
                )
                liked = True
            con.execute(f"COPY l TO '{_esc(tmp)}' (FORMAT PARQUET)")
        finally:
            con.close()
        os.replace(tmp, LIKES_FILE)

    # Update like_count on the owning record
    delta = 1 if liked else -1
    if kind == "thread":
        dt = _get_thread_created_at(target_id)
        if dt:
            path = _parquet_path("threads", dt)
            with _get_lock(path):
                tmp2 = _tmp(path)
                con  = _conn()
                try:
                    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{_esc(path)}')")
                    con.execute(
                        "UPDATE t SET like_count = GREATEST(0, like_count + ?) WHERE thread_id = ?",
                        [delta, target_id]
                    )
                    con.execute(f"COPY t TO '{_esc(tmp2)}' (FORMAT PARQUET)")
                finally:
                    con.close()
                os.replace(tmp2, path)
    else:
        for p in _all_existing_partitions("comments"):
            con = _conn()
            try:
                exists = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{_esc(p)}') WHERE comment_id = ?",
                    [target_id]
                ).fetchone()[0]
            finally:
                con.close()
            if exists:
                with _get_lock(p):
                    tmp2 = _tmp(p)
                    con  = _conn()
                    try:
                        con.execute(f"CREATE TABLE c AS SELECT * FROM read_parquet('{_esc(p)}')")
                        con.execute(
                            "UPDATE c SET like_count = GREATEST(0, like_count + ?) WHERE comment_id = ?",
                            [delta, target_id]
                        )
                        con.execute(f"COPY c TO '{_esc(tmp2)}' (FORMAT PARQUET)")
                    finally:
                        con.close()
                    os.replace(tmp2, p)
                break

    # Return fresh counts from likes file
    con = _conn()
    try:
        count = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_esc(LIKES_FILE)}') WHERE kind=? AND target_id=?",
            [kind, target_id]
        ).fetchone()[0]
        has_liked = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{_esc(LIKES_FILE)}') "
            f"WHERE kind=? AND target_id=? AND user_ip=?",
            [kind, target_id, user_ip]
        ).fetchone()[0]
    finally:
        con.close()
    return {"liked": bool(has_liked), "count": int(count)}


def get_user_likes(user_ip: str) -> list:
    """Return list of target_ids the user has liked."""
    _ensure_likes_parquet()
    con = _conn()
    try:
        rows = con.execute(
            f"SELECT target_id FROM read_parquet('{_esc(LIKES_FILE)}') WHERE user_ip = ?",
            [user_ip]
        ).fetchdf()
        return rows["target_id"].tolist() if not rows.empty else []
    finally:
        con.close()
