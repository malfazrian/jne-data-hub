import csv
import io
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from flask import Flask

from routes import dwr_uploader


def create_index(root: Path, index: Path, files: list[Path]) -> None:
    con = duckdb.connect(str(index))
    con.execute(
        "CREATE TABLE dwr_file_manifest(file_id BIGINT, relative_path VARCHAR, file_size BIGINT, modified_ns BIGINT, awb_count BIGINT, schema_version INTEGER)"
    )
    con.execute("CREATE TABLE dwr_awb_locator(awb VARCHAR, file_id BIGINT)")
    for file_id, path in enumerate(files, start=1):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        awbs = pd.read_parquet(path, columns=["AWB"])["AWB"].tolist()
        con.execute(
            "INSERT INTO dwr_file_manifest VALUES (?, ?, ?, ?, ?, 1)",
            [file_id, relative, stat.st_size, stat.st_mtime_ns, len(set(awbs))],
        )
        con.executemany(
            "INSERT INTO dwr_awb_locator VALUES (?, ?)",
            [(awb, file_id) for awb in sorted(set(awbs))],
        )
    con.close()


def write_partition(root: Path, month: int, rows: list[dict]) -> Path:
    path = root / f"tahun=2026/bulan={month:02d}/data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_extracts_csv_key_header_and_headerless_first_column(tmp_path):
    keyed = tmp_path / "keyed.csv"
    keyed.write_text(" ConNote ,other\n'0001,x\n0002,y\n", encoding="utf-8")
    headerless = tmp_path / "headerless.csv"
    headerless.write_text("0003\n0004\n", encoding="utf-8")

    assert dwr_uploader._extract_connotes(keyed) == ["'0001", "0002"]
    assert dwr_uploader._extract_connotes(headerless) == ["0003", "0004"]


def test_multisheet_reads_only_explicit_key_sheets(tmp_path):
    workbook = tmp_path / "input.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame({"notes": ["ignore"]}).to_excel(writer, sheet_name="Notes", index=False)
        pd.DataFrame({"AWB": ["0001", "0001"]}).to_excel(writer, sheet_name="AWB", index=False)
        pd.DataFrame({" connote ": ["0002"]}).to_excel(writer, sheet_name="More", index=False)

    assert dwr_uploader._extract_connotes(workbook) == ["0001", "0001", "0002"]


def test_multisheet_without_explicit_key_is_rejected(tmp_path):
    workbook = tmp_path / "input.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame({"x": ["0001"]}).to_excel(writer, sheet_name="One", index=False)
        pd.DataFrame({"y": ["0002"]}).to_excel(writer, sheet_name="Two", index=False)

    with pytest.raises(ValueError, match="AWB atau CONNOTE"):
        dwr_uploader._extract_connotes(workbook)


def test_indexed_lookup_preserves_order_duplicates_and_unmatched(tmp_path):
    root = tmp_path / "dwr"
    may = write_partition(
        root,
        5,
        [{"AWB": "'0001", "DWR_AMOUNT_AFTER": 100, "DWR_TANGGAL_AWB": pd.Timestamp("2026-06-13 08:26")}],
    )
    june = write_partition(
        root,
        6,
        [{"AWB": "'0002", "DWR_AMOUNT_AFTER": 200, "DWR_TANGGAL_AWB": pd.Timestamp("2026-06-14 09:00")}],
    )
    index = tmp_path / "index.duckdb"
    create_index(root, index, [may, june])

    columns, rows, matched = dwr_uploader._lookup_dwr_data(
        ["0002", "missing", "0002", "0001"], root, index
    )

    assert columns == ["CONNOTE", "DWR_AMOUNT_AFTER", "DWR_TANGGAL_AWB"]
    assert [row[0] for row in rows] == ["'0002", "'missing", "'0002", "'0001"]
    assert [row[1] for row in rows] == [200, None, 200, 100]
    assert matched == {0, 2, 3}


def test_lookup_rejects_stale_index(tmp_path):
    root = tmp_path / "dwr"
    path = write_partition(root, 5, [{"AWB": "A", "VALUE": 1}])
    index = tmp_path / "index.duckdb"
    create_index(root, index, [path])
    write_partition(root, 5, [{"AWB": "A", "VALUE": 999}, {"AWB": "B", "VALUE": 2}])

    with pytest.raises(dwr_uploader.DwrIndexStaleError):
        dwr_uploader._lookup_dwr_data(["A"], root, index)


def test_result_csv_formats_connote_and_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(dwr_uploader, "TEMP_RESULT_DIR", tmp_path)
    columns = ["CONNOTE", "DWR_AMOUNT_AFTER", "DWR_TANGGAL_AWB"]
    rows = [
        ("'0001", 100, pd.Timestamp("2026-06-13 08:26")),
        ("'missing", None, None),
    ]

    _, path = dwr_uploader._write_result_csv(columns, rows)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        output = list(csv.reader(handle))

    assert output[0] == columns
    assert output[1] == ["'0001", "100", "13/06/2026 08:26:00"]
    assert output[2] == ["'missing", "", ""]


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"))
    app.register_blueprint(dwr_uploader.dwr_uploader_bp)
    app.config.update(TESTING=True)
    monkeypatch.setattr(dwr_uploader, "TEMP_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(dwr_uploader, "TEMP_RESULT_DIR", tmp_path / "results")
    return app.test_client()


def test_page_and_upload_routes_return_result(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dwr_uploader, "_extract_connotes", lambda path: ["0001", "missing"])
    monkeypatch.setattr(
        dwr_uploader,
        "_lookup_dwr_data",
        lambda values, root, index: (
            ["CONNOTE", "DWR_AMOUNT_AFTER"],
            [("'0001", 100), ("'missing", None)],
            {0},
        ),
    )
    result = tmp_path / "results" / "result.csv"

    def fake_write(columns, rows):
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("CONNOTE\n'0001\n", encoding="utf-8")
        return result.name, result

    monkeypatch.setattr(dwr_uploader, "_write_result_csv", fake_write)

    assert client.get("/dwr-uploader").status_code == 200
    response = client.post(
        "/dwr-uploader/upload",
        data={"dwr_file": (io.BytesIO(b"AWB\n0001\n"), "input.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["summary"] == {
        "total_connote_read": 2,
        "total_connote_unique": 2,
        "total_db_match": 1,
        "total_not_match": 1,
    }
    assert client.get(response.get_json()["download_url"]).status_code == 200


def test_upload_validation_and_index_errors(client, monkeypatch):
    assert client.post("/dwr-uploader/upload", data={}).status_code == 400
    unsupported = client.post(
        "/dwr-uploader/upload",
        data={"dwr_file": (io.BytesIO(b"x"), "input.txt")},
        content_type="multipart/form-data",
    )
    assert unsupported.status_code == 400

    monkeypatch.setattr(dwr_uploader, "_extract_connotes", lambda path: ["0001"])
    monkeypatch.setattr(
        dwr_uploader,
        "_lookup_dwr_data",
        lambda *args: (_ for _ in ()).throw(dwr_uploader.DwrIndexStaleError("stale")),
    )
    stale = client.post(
        "/dwr-uploader/upload",
        data={"dwr_file": (io.BytesIO(b"AWB\n0001"), "input.csv")},
        content_type="multipart/form-data",
    )
    assert stale.status_code == 503


def test_download_rejects_unsafe_or_missing_filename(client):
    assert client.get("/dwr-uploader/download/..%2Fsecret.csv").status_code == 400
    assert client.get("/dwr-uploader/download/missing.csv").status_code == 404


def test_pickup_page_links_to_dwr_uploader():
    template = (Path(__file__).parents[1] / "templates" / "pickup_uploader.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/dwr-uploader"' in template


def test_dwr_page_captures_form_data_before_disabling_file_input():
    template = (Path(__file__).parents[1] / "templates" / "dwr_uploader.html").read_text(
        encoding="utf-8"
    )
    form_data_position = template.index("new FormData(form)")
    disabled_position = template.index("fileInput.disabled=true")
    assert form_data_position < disabled_position
