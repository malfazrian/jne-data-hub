import os
from flask import Flask
from waitress import serve
from scripts.utils import auto_update_project_reference
from scripts import backup_syncer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_file_path):
    """Load simple KEY=VALUE pairs from .env into os.environ (without overriding existing env vars)."""
    if not os.path.exists(env_file_path):
        return
    try:
        with open(env_file_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key   = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        print(f"[WARN] Failed to load .env file: {exc}")


def env_int(name, default_value):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default_value
    try:
        return int(value)
    except ValueError:
        return default_value


# ── Bootstrap: load .env BEFORE importing blueprints (they read os.getenv) ───
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_env_file(ENV_FILE)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-before-production")

# ── Register blueprints ───────────────────────────────────────────────────────
from routes.performance    import performance_bp
from routes.report_explorer import report_explorer_bp
from routes.apex_uploader  import apex_uploader_bp
from routes.apex_requester import apex_requester_bp
from routes.pickup_uploader import pickup_uploader_bp
from routes.dwr_uploader import dwr_uploader_bp
from routes.thread_routes  import thread_bp

app.register_blueprint(performance_bp)
app.register_blueprint(report_explorer_bp)
app.register_blueprint(apex_uploader_bp)
app.register_blueprint(apex_requester_bp)
app.register_blueprint(pickup_uploader_bp)
app.register_blueprint(dwr_uploader_bp)
app.register_blueprint(thread_bp)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from routes.shared import REPORT_EXPLORER_BASE

    host    = os.getenv("APP_HOST", "0.0.0.0")
    port    = env_int("APP_PORT", 5000)
    threads = env_int("APP_THREADS", 100)

    auto_update_project_reference()

    _backup_interval = env_int("BACKUP_SYNC_INTERVAL", 300)
    backup_syncer.start_periodic_sync(REPORT_EXPLORER_BASE, interval_seconds=_backup_interval)

    print(f"Server running on http://{host}:{port}")
    serve(app, host=host, port=port, threads=threads)
