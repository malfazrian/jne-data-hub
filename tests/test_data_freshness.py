import datetime
import os
from pathlib import Path

from flask import Flask

from routes import data_freshness


def test_classifies_fresh_warning_stale_and_unknown():
    now = datetime.datetime(2026, 7, 21, 12, 0, 0)

    fresh = data_freshness.classify_freshness(now - datetime.timedelta(hours=2), now)
    assert fresh["status"] == "fresh"
    assert fresh["label"] == "Update"
    assert data_freshness.classify_freshness(now - datetime.timedelta(hours=30), now)["status"] == "warning"
    assert data_freshness.classify_freshness(now - datetime.timedelta(hours=60), now)["status"] == "stale"
    assert data_freshness.classify_freshness(None, now)["status"] == "unknown"


def test_file_timestamp_uses_configured_artifact(tmp_path, monkeypatch):
    artifact = tmp_path / "tracker.json"
    artifact.write_text("{}", encoding="utf-8")
    timestamp = datetime.datetime(2026, 7, 21, 8, 30, 0).timestamp()
    os.utime(artifact, (timestamp, timestamp))
    monkeypatch.setenv("PARQUET_PROCESSED_FILE", str(artifact))

    assert data_freshness.master_data_updated_at().timestamp() == timestamp


def test_freshness_endpoint_returns_source_status(monkeypatch):
    updated_at = datetime.datetime.now() - datetime.timedelta(hours=1)
    monkeypatch.setitem(data_freshness.SOURCE_LOADERS, "dwr", lambda: updated_at)
    data_freshness._freshness_cache.clear()
    app = Flask(__name__)
    app.register_blueprint(data_freshness.data_freshness_bp)

    response = app.test_client().get("/data-freshness/dwr")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "dwr"
    assert payload["status"] == "fresh"
    assert payload["updated_at"]


def test_pages_include_reusable_freshness_chip():
    root = Path(__file__).parents[1] / "templates"
    partial = (root / "_data_freshness.html").read_text(encoding="utf-8")
    assert 'data-freshness-source="{{ freshness_source }}"' in partial
    assert "data_freshness.js" in partial

    for template in ("index.html", "pickup_uploader.html", "dwr_uploader.html"):
        source = (root / template).read_text(encoding="utf-8")
        assert '{% include "_data_freshness.html" %}' in source
