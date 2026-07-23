from pathlib import Path

from flask import Flask

from routes import performance


def test_empty_result_status_marks_job_as_empty():
    status = performance.empty_result_status()

    assert status["done"] is True
    assert status["empty"] is True
    assert status["files"] == []
    assert "tidak ada data" in status["message"]


def test_empty_completed_job_is_retained_and_reported_as_warning():
    request_id = "empty-result"
    performance.progress_status[request_id] = {
        "current": 100,
        "total": 1,
        "files": [],
        "done": True,
        "empty": True,
        "message": "Proses selesai, tetapi tidak ada data untuk project dan periode yang dipilih.",
    }
    app = Flask(__name__)
    app.register_blueprint(performance.performance_bp)

    try:
        response = app.test_client().get(f"/progress_status/{request_id}")
    finally:
        performance.progress_status.pop(request_id, None)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["done"] is True
    assert payload["empty"] is True
    assert payload["files"] == []
    assert payload["message"] == (
        "Proses selesai, tetapi tidak ada data untuk project dan periode yang dipilih."
    )


def test_frontend_checks_final_status_before_showing_download():
    source = (
        Path(__file__).parents[1] / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert "async function renderCompletedJob" in source
    assert "alert-warning" in source
    assert "data.empty" in source
