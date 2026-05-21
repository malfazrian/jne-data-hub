import os
import threading

import pandas as pd


_project_ref_lock = threading.Lock()


def _project_ref_target_path():
    return os.getenv("PROJECT_REF_CSV", os.path.join("data", "project_reference.csv"))


def _file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None

def auto_update_project_reference(force=False):
    src_excel = os.getenv("PROJECT_REF_SOURCE", "")
    if not src_excel:
        return False

    sheet_name = os.getenv("PROJECT_REF_SHEET", "ACC & SHIPPER GROUPING")
    target_csv = _project_ref_target_path()

    with _project_ref_lock:
        src_mtime = _file_mtime(src_excel)
        if src_mtime is None:
            print(f"[WARN] PROJECT_REF_SOURCE tidak bisa diakses: {src_excel}")
            return False

        target_mtime = _file_mtime(target_csv)
        if not force and target_mtime is not None and target_mtime >= src_mtime:
            return False

        target_dir = os.path.dirname(target_csv)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        df = pd.read_excel(src_excel, sheet_name=sheet_name, dtype=str)
        df = df.rename(columns={"CUST_ID_2": "CUST_ID"})
        needed_cols = ["CUST_ID", "CUST_NAME", "BIG_GROUPING_CUST", "CATEGORY"]
        missing = [col for col in needed_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Kolom wajib hilang di PROJECT_REF_SOURCE: {missing}")

        df_out = df[needed_cols]
        tmp_csv = target_csv + ".tmp"
        df_out.to_csv(tmp_csv, index=False, encoding="utf-8-sig")
        os.replace(tmp_csv, target_csv)
        print(f"[OK] project_reference.csv berhasil diupdate dari {src_excel}")
        return True

#PIVOT CONFIGS
pivot_standard = [
                {
                    "name": "PivotStatus",
                    "dest": "B3",
                    "rows": ["STATUS_POD"],
                    "columns": [],
                    "filters": [],
                    "values": [
                        {"field": "AWB", "name": "JUMLAH", "func": "count"}
                    ]
                },
                {
                    "name": "Pivot3LC",
                    "dest": "E3",
                    "rows": ["3 LC DEST"],
                    "columns": [],
                    "filters": [],
                    "values": [
                        {"field": "AWB", "name": "JUMLAH", "func": "count"},
                        {"field": "AMOUNT", "func": "sum", "caption": "JUMLAH_AMOUNT", "num_format": '"IDR" #,##0.00'}
                    ]
                },
                {
                    "name": "PivotCarrer",
                    "dest": "I3",
                    "rows": ["CARRER"],
                    "columns": [],
                    "filters": [],
                    "values": [
                        {"field": "AWB", "name": "JUMLAH", "func": "count"}
                    ]
                }
            ]

pivot_status_by_awb = [
    {
        "name": "PivotStatusByAWB",
        "dest": "B3",
        "rows": ["STATUS_POD"],
        "columns": [],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"},
            {"field": "AWB", "name": "JUMLAH %", "func": "count", "as_percentage": True, "percentage_of": "column"}
        ]
    }
]

pivot_status_by_periode = [
                          {
                            "name": "PivotStatus",
                            "dest": "B3",
                            "rows": ["STATUS_POD"],
                            "columns": ["PERIODE"],
                            "filters": [],
                            "values": [
                                {"field": "AWB", "name": "JUMLAH", "func": "count"}
                            ]
                        }
                      ]

pivot_aj_car = [
    {
        "name": "AllSummary",
        "dest": "A2",
        "rows": ["AJ Car Status", "KETERANGAN AJ CAR", "REASON RETURN"],
        "columns": [],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "Total", "func": "count"}
        ]
    },
    {
        "name": "StatusbyTglEntry",
        "dest": "E2",
        "rows": ["TGL_ENTRY"],
        "columns": ["AJ Car Status"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "Total", "func": "count"}
        ]
    }
]

pivot_status_by_dest = [
                          {
                            "name": "PivotStatusByDest",
                            "dest": "B3",
                            "rows": ["3 LC DEST"],
                            "columns": ["STATUS_POD"],
                            "filters": [],
                            "values": [
                                {"field": "AWB", "name": "JUMLAH", "func": "count"}
                            ]
                        }
                      ]

pivot_status_by_id = [
                          {
                            "name": "PivotStatusById",
                            "dest": "B2",
                            "rows": ["STATUS_POD"],
                            "columns": ["ID_ACCOUNT"],
                            "filters": [],
                            "values": [
                                {"field": "AWB", "name": "JUMLAH", "func": "count"}
                            ]
                        }
                      ]

pivot_status_by_id_tgl_entry = [
                          {
                            "name": "PivotStatusByIdTglEntry",
                            "dest": "B2",
                            "rows": ["ID_ACCOUNT", "STATUS_POD"],
                            "columns": ["TGL_ENTRY"],
                            "filters": [],
                            "values": [
                                {"field": "AWB", "name": "JUMLAH", "func": "count"}
                            ]
                        }
                      ]

pivot_status_by_3LC = [{
                    "name": "PivotStatusBy3LC",
                    "dest": "B2",
                    "rows": ["3 LC DEST"],
                    "columns": ["STATUS_POD"],
                    "filters": [],
                    "values": [
                        {"field": "AWB", "name": "JUMLAH", "func": "count"}
                    ]
                }]

pivot_pzc = [
    {
        "name": "PivotPZC",
        "dest": "B2",
        "rows": ["DEPT PZC"],
        "columns": ["STATUS_POD"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"}
        ]
    }
]

pivot_watson = [
    {
        "name": "PivotStatusWatson",
        "dest": "B2",
        "rows": ["STATUS_POD"],
        "columns": [],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"}
        ]
    },
    {
        "name": "PivotOriginWatson",
        "dest": "E2",
        "rows": ["ORIGIN 2", "ORIGIN"],
        "columns": [],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"}
        ]
    },
    {
        "name": "PivotCarrerPercentWatson",
        "dest": "H2",
        "rows": ["STATUS_POD"],
        "columns": ["CARRER"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "% of Row Total", "func": "count", "as_percentage": True, "percentage_of": "row"}
        ]
    },
    {
        "name": "PivotCarrerWatson",
        "dest": "H20",
        "rows": ["STATUS_POD"],
        "columns": ["CARRER"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"}
        ]
    }
]

pivot_reason_return = [
    {
        "name": "PivotReasonReturn",
        "dest": "B2",
        "rows": ["REASON RETURN"],
        "columns": [],
        "filters": [
            {"field": "STATUS_POD", "value": "Undel"}
        ],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"}
        ]
    }
]

pivot_uob = [
    {
        "name": "PivotCycleNameByStatus",
        "dest": "B2",
        "rows": ["CYCLE NAME"],
        "columns": ["STATUS"],
        "filters": [],
        "values": [
            {"field": "REFF NUM", "name": "JUMLAH", "func": "count"}
        ]
    },
    {
        "name": "PivotCycleNameByKetAWB",
        "dest": "J2",
        "rows": ["CYCLE NAME"],
        "columns": ["KET AWB"],
        "filters": [],
        "values": [
            {"field": "REFF NUM", "name": "JUMLAH", "func": "count"}
        ]
    }
]

pivot_map_aktif = [
    {
        "name": "PivotStatusMapAktif",
        "dest": "B2",
        "rows": ["PERIODE", "WEEK_OF_YEAR"],
        "columns": ["CARRER"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"},
            {"field": "AWB", "name": "% of Row Total", "func": "count", "as_percentage": True, "percentage_of": "row"}
        ]
    }
]

pivot_status_by_tgl_entry = [
    {
        "name": "PivotStatusByTglEntry",
        "dest": "B2",
        "rows": ["STATUS_POD"],
        "columns": ["TGL_ENTRY"],
        "filters": [],
        "values": [
            {"field": "AWB", "name": "JUMLAH", "func": "count"},
            {
                "field": "AWB",
                "name": "% of Row Total",
                "func": "count",
                "as_percentage": True,
                "percentage_of": "column"
            }
        ],
        "sort": {
            "row": "STATUS_POD",
            "by": "% of Row Total",
            "order": "desc"
        }
    }
]
