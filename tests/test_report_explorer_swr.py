import datetime

from routes import report_explorer


def _report_data(name):
    return {"files": [{"name": name}], "tags": [], "exists": True, "from_backup": False}


def test_report_snapshot_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(report_explorer, "REPORT_EXPLORER_SNAPSHOT_DIR", str(tmp_path))
    data = _report_data("cached.xlsx")

    report_explorer._write_report_snapshot("2026-07-21", data, saved_at=123.0)

    assert report_explorer._read_report_snapshot("2026-07-21") == (123.0, data)


def test_stale_snapshot_is_returned_while_refresh_starts(tmp_path, monkeypatch):
    date_str = "2098-07-21"
    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    data = _report_data("stale.xlsx")
    refreshes = []
    monkeypatch.setattr(report_explorer, "REPORT_EXPLORER_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(report_explorer, "REPORT_EXPLORER_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr(report_explorer.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        report_explorer,
        "_start_report_snapshot_refresh",
        lambda *args: refreshes.append(args),
    )
    report_explorer._cache_store.pop(("list_reports", date_str), None)
    report_explorer._write_report_snapshot(date_str, data, saved_at=1.0)

    result = report_explorer._get_list_reports_swr(date_obj, date_str)

    assert result == data
    assert len(refreshes) == 1


def test_missing_snapshot_scans_once_and_persists_result(tmp_path, monkeypatch):
    date_str = "2097-07-21"
    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    fresh = _report_data("fresh.xlsx")
    scans = []
    monkeypatch.setattr(report_explorer, "REPORT_EXPLORER_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(
        report_explorer,
        "_load_list_reports_data",
        lambda *args: scans.append(args) or fresh,
    )
    report_explorer._cache_store.pop(("list_reports", date_str), None)

    result = report_explorer._get_list_reports_swr(date_obj, date_str)

    assert result == fresh
    assert len(scans) == 1
    assert report_explorer._read_report_snapshot(date_str)[1] == fresh
