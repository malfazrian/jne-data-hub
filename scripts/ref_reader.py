import pandas as pd
import os

from scripts.utils import auto_update_project_reference

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_CSV = os.path.join(BASE_DIR, "..", "data", "project_reference.csv")

def _as_str_keep_tick(val):
    s = str(val).strip()
    return s if s.startswith("'") else "'" + s

def normalize_name(name):
    return str(name).strip().replace('\u200b', '').replace('\xa0', '')


def _read_project_csv(csv_path):
    for encoding in ("utf-8-sig", "latin1"):
        try:
            return pd.read_csv(csv_path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(csv_path)


def project_reference_mtime(csv_path="data/project_reference.csv"):
    csv_path = os.getenv("PROJECT_REF_CSV", csv_path)
    try:
        return os.path.getmtime(csv_path)
    except OSError:
        return None


def load_projects(csv_path=None):
    csv_path = csv_path or os.getenv("PROJECT_REF_CSV", "data/project_reference.csv")
    auto_update_project_reference()
    df = _read_project_csv(csv_path)
    required_cols = ["BIG_GROUPING_CUST", "CATEGORY", "CUST_ID", "CUST_NAME"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Kolom wajib hilang di project_reference.csv: {missing}")

    projects = {}
    for _, row in df.iterrows():
        name = normalize_name(row["BIG_GROUPING_CUST"])
        cat  = str(row["CATEGORY"]).strip()
        cid  = _as_str_keep_tick(row["CUST_ID"])
        cname = str(row["CUST_NAME"]).strip() if "CUST_NAME" in df.columns else ""

        if not name:
            continue

        if name not in projects:
            projects[name] = {
                "category": cat,
                "name": name,
                "id_account": []
            }

        if cid and all(it["id"] != cid for it in projects[name]["id_account"]):
            projects[name]["id_account"].append({
                "id": cid,
                "customer": cname
            })

    return projects, sorted(projects.keys())
