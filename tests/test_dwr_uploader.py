import csv
from pathlib import Path

import duckdb
import pandas as pd
import pytest

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
