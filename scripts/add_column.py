import pandas as pd
import numpy as np
import math
import os
import datetime
import glob
import re
from pandas.tseries.offsets import CustomBusinessDay
from dateutil.relativedelta import relativedelta
from .parse_dates import normalize_all_dates

_TABLE_REF = os.getenv("TABLE_REFERENCE_PATH", "")
_HOLIDAY_REF = os.getenv("HOLIDAY_REFERENCE_PATH", "")

def add_origin_city(df: pd.DataFrame, table_reference_path=_TABLE_REF) -> pd.DataFrame:
    # Pastikan kolom ORIGIN ada
    if "ORIGIN" not in df.columns:
        return df

    try:
        # Baca sheet ZONA dari file referensi
        ref_df = pd.read_excel(table_reference_path, sheet_name="ZONA")

        # Pastikan kolom referensi lengkap
        if not {"DEST", "3 LC", "NAMA KAB/KOTA 2", "REGION", "PROVINSI"}.issubset(ref_df.columns):
            print("⚠️ Sheet ZONA tidak memiliki kolom DEST, 3 LC, NAMA KAB/KOTA 2, REGION atau PROVINSI.")
            return df

        # Join left: df.ORIGIN -> ref.DEST (bedakan suffix)
        df = df.merge(
            ref_df[["DEST", "3 LC", "NAMA KAB/KOTA 2", "REGION", "PROVINSI"]],
            how="left",
            left_on="ORIGIN",
            right_on="DEST",
            suffixes=("", "_REF")
        )

        # Rename hasil join
        df.rename(
            columns={
                "NAMA KAB/KOTA 2_REF": "ORIGIN_CITY",
                "PROVINSI": "PROVINSI_ORIGIN",
                "3 LC": "3_LC_ORIGIN"
            },
            inplace=True
        )

        # Drop kolom DEST dari referensi saja
        df.drop(columns=["DEST_REF"], inplace=True, errors="ignore")

    except Exception as e:
        print(f"⚠️ Gagal menambahkan 3_LC_ORIGIN, ORIGIN_CITY, REGION dan PROVINSI_ORIGIN: {e}")

    return df

def add_grouping_late(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_all_dates(df)
    # ---------- Helper ----------
    def dur_hours(td):
        if pd.isna(td): return np.nan
        return td.total_seconds() / 3600
    def dur_days(td):
        if pd.isna(td): return np.nan
        return td.days
    def round_up(x):
        if pd.isna(x): return np.nan
        return math.ceil(x)
    def safe_str(x): return "" if pd.isna(x) else str(x)

    # ---------- Step by Step ----------
    df = add_origin_city(df)
    # ORG_DEST = perbandingan 3_LC_ORIGIN vs 3 LC DEST
    df["ORG_DEST"] = df["3_LC_ORIGIN"] == df["3 LC DEST"]

    # Combine OM HVO
    df["OM_HVO"] = np.where(
        df["OUTBOUND_MANIFEST_DATE"].isna() & df["ORG_DEST"],
        df["1ST_HVO_DATE"], df["OUTBOUND_MANIFEST_DATE"]
    )

    # Combine OM HVO HBG
    df["OM_HVO_HBG"] = np.where(df["OM_HVO"].isna(), df["HBG_DATE"], df["OM_HVO"])

    # Combine PU & Entry
    df["PU_ENTRY"] = np.where(
        (df["PICKUP_STATUS"]=="S01") & (df["PICKUP_DATE"] < df["TGL_ENTRY"]),
        df["PICKUP_DATE"], df["TGL_ENTRY"]
    )

    # Entry - OM
    df["ENTRY_TO_OM"] = (df["OM_HVO_HBG"] - df["TGL_ENTRY"]).apply(dur_hours).apply(round_up)

    # PU - HBG
    df["PU_HBG"] = (df["OM_HVO_HBG"] - df["PU_ENTRY"]).apply(dur_hours).apply(round_up)

    # Remarks ENTRY_OM
    df["REMARKS_ENTRY_OM"] = df["ENTRY_TO_OM"].apply(
        lambda x: None if pd.isna(x) else "FM - Late" if x > 5 else "FM - Ontime"
    )

    # Remarks PU_HBG
    df["REMARKS_PU_HBG"] = df["PU_HBG"].apply(
        lambda x: None if pd.isna(x) else "FM - Late" if x > 5 else "FM - Ontime"
    )

    # IM_HVI
    df["IM_HVI"] = np.where(
        df["INBOUND_MANIFEST_DATE"].isna() & df["ORG_DEST"],
        df["HVI_DATE"], df["INBOUND_MANIFEST_DATE"]
    )

    # IM_HVI_PRA
    df["IM_HVI_PRA"] = np.where(df["IM_HVI"].isna(), df["1ST_RUNSHEET_DATE"], df["IM_HVI"])

    # HBG_IM
    df["HBG_IM"] = (df["IM_HVI_PRA"] - df["OM_HVO_HBG"]).apply(dur_days)

    # Check LT-MM ETD
    df["CHECK_LT_MM_ETD"] = df.apply(
        lambda r: None if pd.isna(r["HBG_IM"])
        else "MM - Late ETD Max" if r["HBG_IM"] > r["ETD"]
        else "check MM", axis=1
    )

    # Check LT-MM
    df["CHECK_LT_MM"] = df["HBG_IM"].apply(
        lambda x: None if pd.isna(x) else "MM - Late > 1 Hari" if x > 1 else "MM - Ontime"
    )

    # REMARKS MM
    df["REMARKS_MM"] = df.apply(
        lambda r: None if pd.isna(r["CHECK_LT_MM_ETD"]) else
                  r["CHECK_LT_MM"] if r["CHECK_LT_MM_ETD"]=="check MM" else
                  r["CHECK_LT_MM_ETD"], axis=1
    )

    # JAM IM
    df["JAM_IM"] = pd.to_datetime(df["IM_HVI"], errors="coerce").dt.hour

    # REMARK IM 5 AB
    df["REMARK_IM_5_1"] = df.apply(
        lambda r: None if pd.isna(r["JAM_IM"]) else
                  "IM < 5 Sore AB" if (r["JAM_IM"]<17 and r["ZONA"] in ["A","B"]) else
                  "IM > 5 Sore AB", axis=1
    )
    # REMARK IM 12 CD
    df["REMARK_IM_5_2"] = df.apply(
        lambda r: None if pd.isna(r["JAM_IM"]) else
                  "IM < 12 Siang CD" if (r["JAM_IM"]<12 and r["ZONA"] in ["C","D"]) else
                  "IM > 12 Siang CD", axis=1
    )

    # RESULT JAM ZONA
    df["REMARK_IM_5"] = df.apply(
        lambda r: r["REMARK_IM_5_1"] if r["ZONA"] in ["A","B"]
        else r["REMARK_IM_5_2"] if r["ZONA"] in ["C","D"]
        else None, axis=1
    )

    # 1st Runsheet_IM HVI PRA
    df["1ST_RUNSHEET_HVI"] = (df["1ST_RUNSHEET_DATE"] - df["IM_HVI_PRA"]).apply(dur_days).apply(round_up)

    # REMARKS DAY
    def remark_day(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "Back Date"
        return "Another Day"
    df["REMARKS_DAY"] = df["1ST_RUNSHEET_HVI"].apply(remark_day)

    # REMARKS <5 Zona A
    df["REMARKS_<5_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - Ontime Zona AB" if (r["REMARK_IM_5_1"]=="IM < 5 Sore AB" and r["REMARKS_DAY"]=="Same Day" and r["ZONA"] in ["A","B"])
                  else "LM - Late Zona AB", axis=1
    )

    # REMARKS >5 A
    df["REMARKS_>5_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY"]=="Back Date" else
                  "LM - Ontime Zona AB" if (r["REMARK_IM_5"]=="IM > 5 Sore AB" and r["ZONA"] in ["A","B"] and r["REMARKS_DAY"] in ["Same Day","Next Day"])
                  else "LM - Late Zona AB", axis=1
    )

    # REMARKS FINAL ZONA A
    df["REMARKS_FINAL_ZONA_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  r["REMARKS_<5_A"] if r["REMARK_IM_5"] in ["IM < 5 Sore AB","IM < 12 Siang CD"] else
                  r["REMARKS_>5_A"], axis=1
    )

    # MTS_IM
    df["MTS_IM"] = (df["MANIFEST_TRANSIT_SUBAGEN_DATE"] - df["IM_HVI_PRA"]).apply(dur_days)

    # REMARKS DAY MTS
    def remark_day_mts(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "LM - IM Backdate"
        return "Another Day"
    df["REMARKS_DAY_MTS"] = df["MTS_IM"].apply(remark_day_mts)

    # REMARKS <5 C D
    df["REMARKS_<5_CD"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY_MTS"]=="LM - IM Backdate" else
                  "Ontime Cabang Utama FW Sub Agen" if (r["REMARK_IM_5"]=="IM < 12 Siang CD" and r["REMARKS_DAY_MTS"]=="Same Day") else
                  "LM - Late FW to Sub Agen", axis=1
    )

    # REMARKS >5 C D
    df["REMARKS_>5_CD"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY_MTS"]=="LM - IM Backdate" else
                  "Ontime Cabang Utama FW Sub Agen" if (r["REMARK_IM_5"]=="IM > 12 Siang CD" and r["REMARKS_DAY_MTS"] in ["Same Day","Next Day"]) else
                  "LM - Late FW to Sub Agen", axis=1
    )

    # FINAL MTS
    df["FINAL_MTS"] = np.where(df["REMARK_IM_5"]=="IM < 5 Sore", df["REMARKS_<5_CD"], df["REMARKS_>5_CD"])

    # FINAL SUBAGEN
    df["FINAL_FW_SUBAGEN"] = np.where(df["ZONA"].isin(["A","B"]), df["REMARKS_FINAL_ZONA_A"], df["FINAL_MTS"])

    # MTS_MTI
    df["MTS_MTI"] = (df["MANIFEST_INBOUND_SUBAGEN_DATE"] - df["MANIFEST_TRANSIT_SUBAGEN_DATE"]).apply(dur_days)

    # REMARK_MTS_MTI
    def remark_mts_mti(r):
        if pd.isna(r): return None
        if r<0: return "LM - MTI Backdate"
        if r>=2: return "LM - Late Inbound Sub Agen"
        return "Ontime Inbound Sub Agen"
    df["REMARK_MTS_MTI"] = df["MTS_MTI"].apply(remark_mts_mti)

    # FINAL FW MTI
    df["FINAL_FW_MTI"] = df.apply(
        lambda r: None if pd.isna(r["FINAL_FW_SUBAGEN"]) else
                  r["REMARK_MTS_MTI"] if r["FINAL_FW_SUBAGEN"]=="Ontime Cabang Utama FW Sub Agen" and pd.notna(r["REMARK_MTS_MTI"]) else
                  r["FINAL_FW_SUBAGEN"], axis=1
    )

    # JAM MTI
    df["JAM_MTI"] = pd.to_datetime(df["MANIFEST_INBOUND_SUBAGEN_DATE"], errors="coerce").dt.hour

    # REMARKS MTI 5
    df["REMARKS_MTI_5"] = df["JAM_MTI"].apply(
        lambda x: None if pd.isna(x) else "MTI < 5 Sore" if x==17 else "MTI > 5 Sore"
    )

    # 1St Runsheet_MTI
    df["1ST_RUNSHEET_MTI"] = (df["1ST_RUNSHEET_DATE"] - df["MANIFEST_INBOUND_SUBAGEN_DATE"]).apply(dur_days)

    # REMARKS DAY MTI
    def remark_day_mti(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "LM - MTI Backdate"
        return "Another Day"
    df["REMARKS_DAY_MTI"] = df["1ST_RUNSHEET_MTI"].apply(remark_day_mti)

    # REMARKS <5 MTI
    df["REMARKS_<5_MTI"] = df.apply(
        lambda r: None if pd.isna(r["REMARKS_MTI_5"]) else
                  "LM - MTI Backdate" if r["REMARKS_DAY_MTI"]=="LM - MTI Backdate" else
                  "LM - Ontime Runsheet Zona C D" if (r["REMARKS_MTI_5"]=="MTI < 5 Sore" and r["REMARKS_DAY_MTI"]=="Same Day") else
                  "LM - Late Runsheet Zona C D", axis=1
    )

    # REMARKS >5 MTI
    df["REMARKS_>5_MTI"] = df.apply(
        lambda r: None if pd.isna(r["REMARKS_MTI_5"]) else
                  "LM - MTI Backdate" if r["REMARKS_DAY_MTI"]=="LM - MTI Backdate" else
                  "LM - Ontime Runsheet Zona C D" if (r["REMARKS_MTI_5"]=="MTI > 5 Sore" and r["REMARKS_DAY_MTI"] in ["Same Day","Next Day"]) else
                  "LM - Late Runsheet Zona C D", axis=1
    )

    # FINAL MTI
    df["FINAL_MTI"] = np.where(df["REMARKS_MTI_5"]=="MTI < 5 Sore", df["REMARKS_<5_MTI"], df["REMARKS_>5_MTI"])

    # FINAL MTI SUBAGEN
    df["FINAL_MTI_SUBAGEN"] = np.where(
        df["FINAL_FW_MTI"].isna(), df["FINAL_MTI"],
        np.where(df["FINAL_FW_MTI"]=="Ontime Inbound Sub Agen", df["FINAL_MTI"], df["FINAL_FW_MTI"])
    )

    # FINAL COMBINE MTI SUBAGEN
    df["FINAL_COMBINE_MTI_SUBAGEN"] = np.where(
        df["FINAL_MTI_SUBAGEN"].isna() & (df["FINAL_FW_MTI"]=="Ontime Cabang Utama FW Sub Agen"),
        "Un MTI", df["FINAL_MTI_SUBAGEN"]
    )

    # FINAL COMBINE LASTMILE
    df["FINAL_COMBINE_LASTMILE"] = np.where(
        (df["FINAL_FW_MTI"]=="Ontime Cabang Utama FW Sub Agen") & (df["FINAL_MTI"].isna()),
        "LM - UnMTI", df["FINAL_COMBINE_MTI_SUBAGEN"]
    )

    # FM / MM / LM flags
    df["FM"] = df["REMARKS_ENTRY_OM"].apply(lambda x: None if pd.isna(x) or "Ontime" in str(x) else x)
    df["MM"] = df["REMARKS_MM"].apply(lambda x: None if pd.isna(x) or "Ontime" in str(x) else x)
    df["LM"] = df["FINAL_COMBINE_LASTMILE"].apply(lambda x: None if pd.isna(x) or "Late" not in str(x) else x)

    # Un-series
    df["Un Rcc"] = df["RECEIVING_DATE"].apply(lambda x: "FM - Unreceiving" if pd.isna(x) else None)
    df["Un Om"] = df["OM_HVO_HBG"].apply(lambda x: "FM - Un Manifest" if pd.isna(x) else None)
    df["Un Im"] = df["IM_HVI_PRA"].apply(lambda x: "MM - Un Inbound" if pd.isna(x) else None)
    df["Un MTS"] = df["MANIFEST_TRANSIT_SUBAGEN_DATE"].apply(lambda x: "LM - Un MTS" if pd.isna(x) else None)
    df["Un MTI"] = df["MANIFEST_INBOUND_SUBAGEN_DATE"].apply(lambda x: "LM - Un MTI" if pd.isna(x) else None)
    df["Un Runsheet"] = df["1ST_RUNSHEET_DATE"].apply(lambda x: "LM - Un Runsheet" if pd.isna(x) else None)

    # Aging Delivery Courier
    df["AGING_DELIVERY_COURIER"] = (df["TGL_RECEIVED"] - df["1ST_RUNSHEET_DATE"]).apply(dur_days)
    df["POD_COURIER"] = df["AGING_DELIVERY_COURIER"].apply(
        lambda x: None if pd.isna(x) else "LM - Late Update POD" if x > 1 else None
    )

    # FD Undel
    df["FD_UNDEL"] = df["DATE_LAST_ATTEMPT"].apply(lambda x: "LM - Late FD (Undel)" if pd.notna(x) else None)

    # Cancel
    df["CANCEL"] = df["STATUS_POD"].apply(
        lambda x: None if pd.isna(x) else "FM - AWB Cancel" if "Cancel" in str(x) else None
    )

    # FW
    df["FW"] = df["NO_CNOTE_FW"].apply(
        lambda x: None if pd.isna(x) else "LM - Problem Misroute" if "FW" in str(x) else None
    )

    # Cukai
    df["CUKAI"] = df["HOLD_REASON"].apply(
        lambda x: None if pd.isna(x) else "MM - Late Problem Bea Cukai"
        if ("Cukai" in str(x) or "Bea" in str(x)) else None
    )

    df["GABUNGAN_ANALISA"] = (
        df[["FM","MM","LM","Un Rcc","Un Om","Un Im","Un MTS","Un MTI",
            "Un Runsheet","CARRER_POD","POD_COURIER","FD_UNDEL",
            "CANCEL","FW","CUKAI"]]
        .map(safe_str)
        .agg(", ".join, axis=1)
    )

    # Flagging FM/MM/LM late
    df["FM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "FM - LATE" if "FM" in x else None
    )
    df["MM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "MM - LATE" if "MM" in x else None
    )
    df["LM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "LM - LATE" if "LM" in x else None
    )

    df["GROUPING_LATE"] = (
        df[["FM_LATE", "MM_LATE", "LM_LATE"]]
        .apply(lambda row: ", ".join([v for v in row if v not in [None, ""]]), axis=1)
    )

    # Hanya isi kalau CARRER_POD == "Over SLA", selain itu kosong
    df.loc[~df["CARRER"].isin(["Over SLA", "Over SLA (became)"]), "GROUPING_LATE"] = ""
    df.loc[(df["CARRER"] == "Over SLA") & (df["GROUPING_LATE"].isna() | (df["GROUPING_LATE"] == "")), "GROUPING_LATE"] = "LM - LATE"
    
    # Bersihkan kolom bantu
    df = df.drop(
        columns=[
            # gabungan akhir
            "GABUNGAN_ANALISA","FM_LATE","MM_LATE","LM_LATE",
            # remark FM/MM/LM awal
            "FM","MM","LM","Un Rcc","Un Om","Un Im","Un MTS","Un MTI","Un Runsheet",
            "Carrer POD","POD Courier","FD Undel","Cancel","FW","Cukai",
            "FINAL_COMBINE_LASTMILE","FINAL_COMBINE_MTI_SUBAGEN","FINAL_MTI_SUBAGEN",
            "FINAL_MTI","REMARKS_>5_MTI","REMARKS_<5_MTI","REMARKS_DAY_MTI",
            "1ST_RUNSHEET_MTI","REMARKS_MTI_5","JAM_MTI","FINAL_FW_MTI",
            "REMARK_MTS_MTI","MTS_MTI","FINAL_FW_SUBAGEN",
            "POD_COURIER","AGING_DELIVERY_COURIER",
            # blok ORG_DEST sampai FINAL_MTS
            "ORG_DEST","OM_HVO","OM_HVO_HBG","PU_ENTRY","ENTRY_TO_OM","PU_HBG",
            "REMARKS_ENTRY_OM","REMARKS_PU_HBG",
            "IM_HVI","IM_HVI_PRA","HBG_IM","CHECK_LT_MM_ETD","CHECK_LT_MM",
            "REMARKS_MM","JAM_IM","REMARK_IM_5_1","REMARK_IM_5_2","REMARK_IM_5",
            "1ST_RUNSHEET_HVI","REMARKS_DAY","REMARKS_<5_A","REMARKS_>5_A",
            "REMARKS_FINAL_ZONA_A","MTS_IM","REMARKS_DAY_MTS",
            "REMARKS_<5_CD","REMARKS_>5_CD","FINAL_MTS"
        ],
        errors="ignore"
    )

    return df

def resolve_col(df, options, default=pd.NA):
    """
    Cari nama kolom dari daftar `options` yang ada di df.
    Kalau tidak ketemu → buat kolom baru pakai nama pertama.
    """
    for col in options:
        if col in df.columns:
            return col
    # kalau semua ga ada → buat kolom pertama
    df[options[0]] = default
    return options[0]

def add_aging_carrer(df: pd.DataFrame, 
                     holiday_path=_HOLIDAY_REF,
                     debug=False) -> pd.DataFrame:

    df = df.copy()

    # --- Resolve kolom dengan dual name ---
    col_1st_attempt      = resolve_col(df, ["1ST ATTEMPT", "1ST_ATTEMPT_DATE"])
    col_aging_1st        = resolve_col(df, ["AGING 1st ATTEMPT", "AGING_1ST"])
    col_career_1st       = resolve_col(df, ["CAREER 1st ATTEMPT", "CAREER_1ST"])
    col_aging_pod        = resolve_col(df, ["AGING POD", "AGING_POD"])
    col_carrer_pod       = resolve_col(df, ["CARRER POD", "CARRER_POD"])

    for col in ["TGL_ENTRY", col_1st_attempt, "TGL_RECEIVED"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    df[col_1st_attempt] = df[col_1st_attempt].fillna(df["TGL_RECEIVED"])
    df["ETD"] = df["ETD"].fillna(30)  # default ETD 30 hari kalau kosong

    # --- Load holiday list ---
    try:
        holidays = pd.read_excel(holiday_path, sheet_name="Holiday", usecols="A")
        holidays = pd.to_datetime(holidays.iloc[:, 0], errors="coerce").dropna().dt.normalize()
        holidays = holidays.values.astype("datetime64[D]")
        if debug:
            print(f"[i] Holidays loaded: {len(holidays)} hari libur")
    except Exception as e:
        print(f"[!] Gagal baca holiday file: {e}")
        holidays = np.array([], dtype="datetime64[D]")

    # --- Helper: networkdays ---
    def networkdays(start, end):
        if pd.isna(start) or pd.isna(end):
            return pd.NA

        start_date = np.datetime64(pd.to_datetime(start).normalize(), "D")
        end_date   = np.datetime64(pd.to_datetime(end).normalize(), "D")

        if end_date <= start_date:
            return 0

        return np.busday_count(start_date, end_date, holidays=holidays)

    # --- NORMALISASI EMPTY STRING → NA ---
    for col in [col_aging_1st, col_career_1st, col_aging_pod, col_carrer_pod]:
        if col in df.columns:
            df[col] = df[col].replace(r"^\s*$", pd.NA, regex=True)

    # --- Define mask for Success ---
    mask_success = df["STATUS_POD_2"].astype(str).str.lower().eq("success")
    mask_non_success = ~mask_success

    # --- FILL 1ST ATTEMPT JIKA KOSONG TAPI TGL_RECEIVED ADA ---
    if col_1st_attempt in df.columns and "TGL_RECEIVED" in df.columns:
        mask_fill_1st = (
            mask_success &
            df[col_1st_attempt].isna() &
            df["TGL_RECEIVED"].notna()
        )

        if debug and mask_fill_1st.any():
            print(f"[DEBUG] Fill {mask_fill_1st.sum()} baris: "
                  f"1ST_ATTEMPT kosong → diisi dari TGL_RECEIVED")

        df.loc[mask_fill_1st, col_1st_attempt] = df.loc[mask_fill_1st, "TGL_RECEIVED"]

    # --- Clear untuk non-success ---
    for col in [col_1st_attempt, col_aging_1st, col_career_1st, col_aging_pod, col_carrer_pod]:
        if col in df.columns:
            df.loc[mask_non_success, col] = pd.NA

    # --- AGING 1st ATTEMPT ---
    mask_recalc = mask_success
    vals = []
    indices = []
    for idx, row in df.loc[mask_recalc].iterrows():
        try:
            if debug:
                print(
                    f"[DEBUG AGING 1ST] idx={idx} awb={row.get('AWB')} "
                    f"TGL_ENTRY={row['TGL_ENTRY']} "
                    f"1ST_ATTEMPT={row[col_1st_attempt]}"
                )

            v = networkdays(row["TGL_ENTRY"], row[col_1st_attempt])
        except Exception as e:
            if debug:
                print(f"[DEBUG networkdays exception] idx={idx} awb={row.get('AWB')} start={row.get('TGL_ENTRY')} end={row.get(col_1st_attempt)} err={e}")
            v = pd.NA
        vals.append(v)
        indices.append(idx)
    # assign back preserving index order
    try:
        df.loc[indices, col_aging_1st] = vals
    except Exception:
        # fallback to original apply if assignment fails
        df.loc[mask_recalc, col_aging_1st] = df[mask_recalc].apply(
            lambda row: networkdays(row["TGL_ENTRY"], row[col_1st_attempt]), axis=1
        )

    # --- CAREER 1st ATTEMPT ---
    df.loc[mask_success, col_career_1st] = np.where(
        df.loc[mask_success, col_aging_1st] <= df.loc[mask_success, "ETD"],
        "Ontime SLA",
        "Over SLA"
    )

    # --- AGING POD ---
    vals = []
    indices = []

    for idx, row in df.loc[mask_success].iterrows():
        if debug:
            print(
                f"[DEBUG AGING POD] idx={idx} awb={row.get('AWB')} "
                f"TGL_ENTRY={row['TGL_ENTRY']} "
                f"TGL_RECEIVED={row['TGL_RECEIVED']}"
            )

        v = networkdays(row["TGL_ENTRY"], row["TGL_RECEIVED"])
        vals.append(v)
        indices.append(idx)

    df.loc[indices, col_aging_pod] = vals

    # --- CARRER POD ---
    df.loc[mask_success, col_carrer_pod] = np.where(
        df.loc[mask_success, col_aging_pod] <= df.loc[mask_success, "ETD"],
        "Ontime SLA",
        "Over SLA"
    )

    # --- Clamp negative numeric aging values to 0 (unexpected negatives may appear due to data issues)
    try:
        for aging_col in [col_aging_1st, col_aging_pod]:
            if aging_col in df.columns:
                # coerce to numeric if possible and aggressively remove negatives
                df[aging_col] = pd.to_numeric(df[aging_col], errors="coerce")

                # first pass: set any explicit negative numeric values to 0
                neg_mask = df[aging_col].notna() & (df[aging_col] < 0)
                if debug and neg_mask.any():
                    print(f"[DEBUG add_aging_carrer] negative values in {aging_col}: {df.loc[neg_mask, ['AWB', aging_col]].to_dict(orient='records')}")
                df.loc[neg_mask, aging_col] = 0

                # second pass: ensure no remaining negative-like strings slipped through
                # (e.g. '-1', '-1.0' as text) — re-coerce and clip
                df[aging_col] = pd.to_numeric(df[aging_col], errors="coerce")
                df[aging_col] = df[aging_col].clip(lower=0)

                # final check: report if any negatives remain (should be none)
                final_neg_mask = df[aging_col].notna() & (df[aging_col] < 0)
                if debug and final_neg_mask.any():
                    print(f"[WARN add_aging_carrer] after clamp, still negative in {aging_col}: {df.loc[final_neg_mask, ['AWB', aging_col]].to_dict(orient='records')}")
    except Exception as e:
        if debug:
            print(f"[ERROR add_aging_carrer] error while clamping aging columns: {e}")
        pass

    # =========================================================
    # FINAL SAFETY NET (FOR SUCCESS ONLY)
    # =========================================================
    mask_success = df["STATUS_POD_2"].astype(str).str.lower().eq("success")

    # --- AGING: NA → 0 ---
    for aging_col in [col_aging_1st, col_aging_pod]:
        if aging_col in df.columns:
            df.loc[
                mask_success & df[aging_col].isna(),
                aging_col
            ] = 0

    # --- NORMALISASI SEMUA PSEUDO-NA KE pd.NA ---
    for col in [col_career_1st, col_carrer_pod]:
        if col in df.columns:
            df[col] = (
                df[col]
                .replace(
                    to_replace=[
                        "<NA>", "NA", "NaN", "nan", "", " "
                    ],
                    value=pd.NA
                )
            )

    # --- CAREER: NA → Ontime SLA ---
    for career_col in [col_career_1st, col_carrer_pod]:
        if career_col in df.columns:
            df.loc[
                mask_success & df[career_col].isna(),
                career_col
            ] = "Ontime SLA"

    return df

# helper untuk conditional kolom
def add_column(df, column_name, func):
    try:
        df[column_name] = df.apply(func, axis=1)
        return df
    except Exception as e:
        raise ValueError(f"Error adding column {column_name}: {e}")

def add_status_pod_2(df, debug: bool = False):
    df = df.copy()

    # Check if required columns exist
    required_columns = [
        'STATUS_POD_UPDATE'
    ]
    missing_required = [col for col in required_columns if col not in df.columns]
    if missing_required:
        raise ValueError(f"Missing required columns: {missing_required}")

    if "STATUS_POD_2" in df.columns:
        df = df.drop(columns=["STATUS_POD_2"])

    if debug:
        print("Kolom awal:", df.columns.tolist())
        print(f"STATUS_POD_UPDATE unique values: {df['STATUS_POD_UPDATE'].unique().tolist()}")

    df["STATUS_POD_2"] = df["STATUS_POD_UPDATE"]

    if debug:
        print("Kolom akhir:", df.columns.tolist())
        print(f"STATUS_POD_2 values: {df['STATUS_POD_2'].unique().tolist()}")

    return df

def fix_regional_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Revisi semua kolom yang mengandung kata 'REGIONAL'
    - Replace 'Sumatara' (case-insensitive) menjadi 'Sumatera'
    """
    for col in df.columns:
        if "REGIONAL" in col.upper():
            # Pastikan kolom bertipe string
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"(?i)sumatara", "Sumatera", regex=True)
            )
    return df

INDO_MONTHS = [
    "", "JANUARI", "FEBRUARI", "MARET", "APRIL", "MEI", "JUNI",
    "JULI", "AGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DESEMBER"
]

def add_address(df: pd.DataFrame, name: str) -> pd.DataFrame:
    df = df.copy()
    addr_cols = ["ADDR1", "ADDR2", "ADDR3"]
    for col in addr_cols:
        if col not in df.columns:
            df[col] = ""

    # hanya buat kolom ADDRESS kalau belum ada
    if name not in df.columns or df[name].isna().all():
        df[name] = (
            df[addr_cols]
            .fillna("")
            .astype(str)
            .apply(lambda row: " ".join(x for x in row if x.strip()), axis=1)
        )
        
    return df

def add_status_pod(df: pd.DataFrame, debug: bool = False) -> pd.DataFrame:
    df = df.copy()

    # Check if required columns exist
    required_columns = [
        'STATUS_POD_UPDATE',
    ]
    missing_required = [col for col in required_columns if col not in df.columns]
    if missing_required:
        raise ValueError(f"Missing required columns: {missing_required}")

    if debug:
        print("Kolom awal:", df.columns.tolist())
        print(f"STATUS_POD_UPDATE unique values: {df['STATUS_POD_UPDATE'].unique().tolist()}")

    # -----------------------------
    # Replace STATUS_POD with STATUS_POD_UPDATE
    # -----------------------------
    if "STATUS_POD_UPDATE" in df.columns:
        if "STATUS_POD" in df.columns:
            df = df.drop(columns=["STATUS_POD"])
        df = df.rename(columns={"STATUS_POD_UPDATE": "STATUS_POD"})

    if debug:
        print("Kolom akhir:", df.columns.tolist())
        print(f"STATUS_POD values: {df['STATUS_POD'].unique().tolist()}")

    return df

def add_cust_name(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "GROUPING_SHIPPER" in df.columns:
        df = df.rename(columns={"GROUPING_SHIPPER": "CUST_NAME"})
    return df

def add_reason_undel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "REASON RETURN" in df.columns:
        df = df.rename(columns={"REASON RETURN": "REASON UNDEL"})
    return df

def add_periode(df: pd.DataFrame, debug=False) -> pd.DataFrame:
    df = df.copy()
    for col in ["PERIODE", "WEEK", "WEEK_OF_YEAR", "DAY"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if "TGL_ENTRY" not in df.columns:
        print("❌ Kolom 'TGL_ENTRY' tidak ditemukan.")
        return df

    # Pastikan TGL_ENTRY datetime
    if not pd.api.types.is_datetime64_any_dtype(df["TGL_ENTRY"]):
        if debug:
            print("add_periode: TGL_ENTRY belum datetime, mencoba konversi fallback...")
        df["TGL_ENTRY"] = pd.to_datetime(df["TGL_ENTRY"], errors="coerce")

    # Buat kolom PERIODE (nama bulan)
    df["PERIODE"] = df["TGL_ENTRY"].apply(
        lambda val: INDO_MONTHS[val.month] if pd.notna(val) and 1 <= val.month <= 12 else ""
    )

    # Buat kolom WEEK (minggu ke berapa dalam bulan)
    def week_of_month(dt):
        if pd.isna(dt):
            return None
        first_day = dt.replace(day=1)
        return ((dt - first_day).days // 7) + 1

    df["WEEK"] = df["TGL_ENTRY"].apply(week_of_month)

    # Buat kolom WEEK_OF_YEAR (minggu ISO, 1–52/53)
    df["WEEK_OF_YEAR"] = df["TGL_ENTRY"].dt.isocalendar().week

    # Buat kolom DAY (tanggal saja, 1–31)
    df["DAY"] = df["TGL_ENTRY"].dt.day

    if debug:
        print(f"add_periode: sample: {df[['TGL_ENTRY','PERIODE','WEEK','WEEK_OF_YEAR','DAY']].head(3).to_dict('records')}")

    return df

def add_time_received(df: pd.DataFrame, debug=False) -> pd.DataFrame:
    df = df.copy()
    for col in ["TIME_RECEIVED"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if "TGL_RECEIVED" not in df.columns:
        print("❌ Kolom 'TGL_RECEIVED' tidak ditemukan.")
        return df

    # Pastikan TGL_RECEIVED datetime
    if not pd.api.types.is_datetime64_any_dtype(df["TGL_RECEIVED"]):
        if debug:
            print("add_time_received: TGL_RECEIVED belum datetime, mencoba konversi fallback...")
        df["TGL_RECEIVED"] = pd.to_datetime(df["TGL_RECEIVED"], errors="coerce")

    # Buat kolom TIME_RECEIVED
    df["TIME_RECEIVED"] = df["TGL_RECEIVED"].dt.strftime("%H:%M:%S")

    if debug:
        print(f"add_time_received: sample: {df[['TGL_RECEIVED','TIME_RECEIVED']].head(3).to_dict('records')}")

    return df

def add_status_latlong(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "STATUS_LATITUDE" not in df.columns or "STATUS_LONGITUDE" not in df.columns:
        return df

    def combine_latlong(row):
        lat = str(row["STATUS_LATITUDE"]).strip() if pd.notna(row["STATUS_LATITUDE"]) else ""
        lon = str(row["STATUS_LONGITUDE"]).strip() if pd.notna(row["STATUS_LONGITUDE"]) else ""
        if lat and lon and lat.lower() != "nan" and lon.lower() != "nan":
            return f"{lat}, {lon}"
        return ""

    df["STATUS_LATLONG"] = df.apply(combine_latlong, axis=1)
    return df

def add_origin_city(df: pd.DataFrame, ref=_TABLE_REF) -> pd.DataFrame:
    # Pastikan kolom ORIGIN ada
    if "ORIGIN" not in df.columns:
        return df

    try:
        # Baca sheet ZONA dari file referensi
        ref_df = pd.read_excel(ref, sheet_name="ZONA")

        # Pastikan kolom referensi lengkap
        if not {"DEST", "3 LC", "NAMA KAB/KOTA", "NAMA KAB/KOTA 2", "REGION", "PROVINSI", "KODE POS"}.issubset(ref_df.columns):
            print("⚠️ Sheet ZONA tidak memiliki kolom DEST, 3 LC, NAMA KAB/KOTA, NAMA KAB/KOTA 2, REGION, PROVINSI atau KODE POS.")
            return df

        # Join left: df.ORIGIN -> ref.DEST (bedakan suffix)
        df = df.merge(
            ref_df[["DEST", "3 LC", "NAMA KAB/KOTA", "NAMA KAB/KOTA 2", "REGION", "PROVINSI", "KODE POS"]],
            how="left",
            left_on="ORIGIN",
            right_on="DEST",
            suffixes=("", "_REF")
        )

        # Rename hasil join
        df.rename(
            columns={
                "NAMA KAB/KOTA": "ORIGIN_CITY",
                "NAMA KAB/KOTA 2_REF": "ORIGIN_CITY_2",
                "PROVINSI": "PROVINSI_ORIGIN",
                "3 LC": "3_LC_ORIGIN",
                "KODE POS": "KODE_POS_ORI"
            },
            inplace=True
        )

        # Drop kolom DEST dari referensi saja
        df.drop(columns=["DEST_REF"], inplace=True, errors="ignore")

    except Exception as e:
        print(f"⚠️ Gagal menambahkan 3_LC_ORIGIN, ORIGIN_CITY, ORIGIN_CITY_2, REGION, PROVINSI_ORIGIN dan KODE_POS_ORI: {e}")

    return df

def add_province_zipcode(df: pd.DataFrame, ref=_TABLE_REF) -> pd.DataFrame:
    df = df.copy()

    if "DEST" not in df.columns:
        print("⚠️ Sheet MASTER tidak memiliki kolom DEST.")
        return df

    try:
        ref_df = pd.read_excel(ref, sheet_name="ZONA")

        if not {"DEST", "KECAMATAN", "NAMA KAB/KOTA", "PROVINSI", "KODE POS"}.issubset(ref_df.columns):
            print("⚠️ Sheet ZONA tidak memiliki kolom DEST, KECAMATAN, NAMA KAB/KOTA, PROVINSI atau KODE POS.")
            return df

        # Pastikan sama-sama string dan tanpa spasi
        df["DEST"] = df["DEST"].astype(str).str.strip()
        ref_df["DEST"] = ref_df["DEST"].astype(str).str.strip()

        # Hilangkan duplikat di referensi
        ref_df_unique = ref_df.drop_duplicates(subset=["DEST"])[["DEST", "KECAMATAN", "NAMA KAB/KOTA", "PROVINSI", "KODE POS"]]

        # Merge berdasarkan DEST
        df = df.merge(
            ref_df_unique,
            how="left",
            on="DEST"
        )

    except Exception as e:
        print(f"⚠️ Gagal menambahkan KECAMATAN, NAMA KAB/KOTA, PROVINSI dan KODE POS: {e}")

    return df

def add_eta(
    df: pd.DataFrame, 
    holiday_path=_HOLIDAY_REF, 
    holidayExcl=True
) -> pd.DataFrame:
    df = df.copy()
    
    if "ETD" not in df.columns or "TGL_ENTRY" not in df.columns:
        print("⚠️ Kolom 'ETD' atau 'TGL_ENTRY' tidak ditemukan, tidak bisa menambahkan ETA.")
        return df

    # Pastikan TGL_ENTRY datetime
    df["TGL_ENTRY"] = pd.to_datetime(df["TGL_ENTRY"], errors="coerce")

    # Default tidak ada holidays
    holidays = []
    if holidayExcl:
        try:
            holidays = pd.read_excel(
                holiday_path, 
                sheet_name="Holiday", 
                usecols=[0], 
                header=None
            )[0]
            holidays = pd.to_datetime(holidays, errors="coerce").dropna().tolist()
        except Exception as e:
            print(f"⚠️ Gagal load Holiday.xlsx: {e}")
            holidays = []

    try:
        df["ETD_days"] = pd.to_numeric(df["ETD"], errors="coerce")

        if holidayExcl:
            # Custom business day offset (skip weekend + holiday)
            cbd = CustomBusinessDay(holidays=holidays)
            df["ETA"] = df.apply(
                lambda row: row["TGL_ENTRY"] + cbd * (int(row["ETD_days"]) + 1)
                if pd.notna(row["TGL_ENTRY"]) and pd.notna(row["ETD_days"]) else pd.NaT,
                axis=1
            )
        else:
            # Kalender normal (termasuk weekend & libur)
            df["ETA"] = df["TGL_ENTRY"] + pd.to_timedelta(df["ETD_days"] + 1, unit="D")

    except Exception as e:
        print(f"⚠️ Gagal menghitung ETA: {e}")
        df["ETA"] = pd.NaT

    df.drop(columns=["ETD_days"], inplace=True, errors="ignore")
    
    return df

def add_coding_delivery(df: pd.DataFrame, ref=_TABLE_REF) -> pd.DataFrame:
    df = df.copy()
    
    if "CODING" not in df.columns:
        print("⚠️ Kolom 'CODING' tidak ditemukan, tidak bisa menambahkan CODING_DELIVERY.")
        return df

    try:
        ref_df = pd.read_excel(ref, sheet_name="STATUS_CCC", header=1)
        if not {"CODE", "STATUS"}.issubset(ref_df.columns):
            print("⚠️ Sheet STATUS_CCC tidak memiliki kolom CODE atau STATUS.")
            return df

        # Buat kolom default kosong
        df["CODING_DELIVERY"] = None  

        # Ambil hanya baris dengan CODING diawali 'D'
        mask_d = df["CODING"].astype(str).str.startswith("D", na=False)
        df_d = df.loc[mask_d].merge(
            ref_df[["CODE", "STATUS"]],
            how="left",
            left_on="CODING",
            right_on="CODE"
        )

        # Update kolom CODING_DELIVERY hanya untuk yang CODING diawali D
        df.loc[mask_d, "CODING_DELIVERY"] = df_d["STATUS"].values

    except Exception as e:
        print(f"⚠️ Gagal menambahkan informasi CODING_DELIVERY: {e}")

    return df

def add_grouping_late(df: pd.DataFrame) -> pd.DataFrame:
    # ---------- Helper ----------
    def dur_hours(td):
        if pd.isna(td): return np.nan
        return td.total_seconds() / 3600
    def dur_days(td):
        if pd.isna(td): return np.nan
        return td.days
    def round_up(x):
        if pd.isna(x): return np.nan
        return math.ceil(x)
    def safe_str(x): return "" if pd.isna(x) else str(x)

    # ---------- Step by Step ----------
    df = add_origin_city(df)
    # ORG_DEST = perbandingan 3_LC_ORIGIN vs 3 LC DEST
    df["ORG_DEST"] = df["3_LC_ORIGIN"] == df["3 LC DEST"]

    # Combine OM HVO
    df["OM_HVO"] = np.where(
        df["OUTBOUND_MANIFEST_DATE"].isna() & df["ORG_DEST"],
        df["1ST_HVO_DATE"], df["OUTBOUND_MANIFEST_DATE"]
    )

    # Combine OM HVO HBG
    df["OM_HVO_HBG"] = np.where(df["OM_HVO"].isna(), df["HBG_DATE"], df["OM_HVO"])

    # Combine PU & Entry
    df["PU_ENTRY"] = np.where(
        (df["PICKUP_STATUS"]=="S01") & (df["PICKUP_DATE"] < df["TGL_ENTRY"]),
        df["PICKUP_DATE"], df["TGL_ENTRY"]
    )

    # Entry - OM
    df["ENTRY_TO_OM"] = (df["OM_HVO_HBG"] - df["TGL_ENTRY"]).apply(dur_hours).apply(round_up)

    # PU - HBG
    df["PU_HBG"] = (df["OM_HVO_HBG"] - df["PU_ENTRY"]).apply(dur_hours).apply(round_up)

    # Remarks ENTRY_OM
    df["REMARKS_ENTRY_OM"] = df["ENTRY_TO_OM"].apply(
        lambda x: None if pd.isna(x) else "FM - Late" if x > 5 else "FM - Ontime"
    )

    # Remarks PU_HBG
    df["REMARKS_PU_HBG"] = df["PU_HBG"].apply(
        lambda x: None if pd.isna(x) else "FM - Late" if x > 5 else "FM - Ontime"
    )

    # IM_HVI
    df["IM_HVI"] = np.where(
        df["INBOUND_MANIFEST_DATE"].isna() & df["ORG_DEST"],
        df["HVI_DATE"], df["INBOUND_MANIFEST_DATE"]
    )

    # IM_HVI_PRA
    df["IM_HVI_PRA"] = np.where(df["IM_HVI"].isna(), df["1ST_RUNSHEET_DATE"], df["IM_HVI"])

    # HBG_IM
    df["HBG_IM"] = (df["IM_HVI_PRA"] - df["OM_HVO_HBG"]).apply(dur_days)

    # Check LT-MM ETD
    df["CHECK_LT_MM_ETD"] = df.apply(
        lambda r: None if pd.isna(r["HBG_IM"])
        else "MM - Late ETD Max" if r["HBG_IM"] > r["ETD"]
        else "check MM", axis=1
    )

    # Check LT-MM
    df["CHECK_LT_MM"] = df["HBG_IM"].apply(
        lambda x: None if pd.isna(x) else "MM - Late > 1 Hari" if x > 1 else "MM - Ontime"
    )

    # REMARKS MM
    df["REMARKS_MM"] = df.apply(
        lambda r: None if pd.isna(r["CHECK_LT_MM_ETD"]) else
                  r["CHECK_LT_MM"] if r["CHECK_LT_MM_ETD"]=="check MM" else
                  r["CHECK_LT_MM_ETD"], axis=1
    )

    # JAM IM
    df["JAM_IM"] = pd.to_datetime(df["IM_HVI"], errors="coerce").dt.hour

    # REMARK IM 5 AB
    df["REMARK_IM_5_1"] = df.apply(
        lambda r: None if pd.isna(r["JAM_IM"]) else
                  "IM < 5 Sore AB" if (r["JAM_IM"]<17 and r["ZONA"] in ["A","B"]) else
                  "IM > 5 Sore AB", axis=1
    )
    # REMARK IM 12 CD
    df["REMARK_IM_5_2"] = df.apply(
        lambda r: None if pd.isna(r["JAM_IM"]) else
                  "IM < 12 Siang CD" if (r["JAM_IM"]<12 and r["ZONA"] in ["C","D"]) else
                  "IM > 12 Siang CD", axis=1
    )

    # RESULT JAM ZONA
    df["REMARK_IM_5"] = df.apply(
        lambda r: r["REMARK_IM_5_1"] if r["ZONA"] in ["A","B"]
        else r["REMARK_IM_5_2"] if r["ZONA"] in ["C","D"]
        else None, axis=1
    )

    # 1st Runsheet_IM HVI PRA
    df["1ST_RUNSHEET_HVI"] = (df["1ST_RUNSHEET_DATE"] - df["IM_HVI_PRA"]).apply(dur_days).apply(round_up)

    # REMARKS DAY
    def remark_day(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "Back Date"
        return "Another Day"
    df["REMARKS_DAY"] = df["1ST_RUNSHEET_HVI"].apply(remark_day)

    # REMARKS <5 Zona A
    df["REMARKS_<5_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - Ontime Zona AB" if (r["REMARK_IM_5_1"]=="IM < 5 Sore AB" and r["REMARKS_DAY"]=="Same Day" and r["ZONA"] in ["A","B"])
                  else "LM - Late Zona AB", axis=1
    )

    # REMARKS >5 A
    df["REMARKS_>5_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY"]=="Back Date" else
                  "LM - Ontime Zona AB" if (r["REMARK_IM_5"]=="IM > 5 Sore AB" and r["ZONA"] in ["A","B"] and r["REMARKS_DAY"] in ["Same Day","Next Day"])
                  else "LM - Late Zona AB", axis=1
    )

    # REMARKS FINAL ZONA A
    df["REMARKS_FINAL_ZONA_A"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  r["REMARKS_<5_A"] if r["REMARK_IM_5"] in ["IM < 5 Sore AB","IM < 12 Siang CD"] else
                  r["REMARKS_>5_A"], axis=1
    )

    # MTS_IM
    df["MTS_IM"] = (df["MANIFEST_TRANSIT_SUBAGEN_DATE"] - df["IM_HVI_PRA"]).apply(dur_days)

    # REMARKS DAY MTS
    def remark_day_mts(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "LM - IM Backdate"
        return "Another Day"
    df["REMARKS_DAY_MTS"] = df["MTS_IM"].apply(remark_day_mts)

    # REMARKS <5 C D
    df["REMARKS_<5_CD"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY_MTS"]=="LM - IM Backdate" else
                  "Ontime Cabang Utama FW Sub Agen" if (r["REMARK_IM_5"]=="IM < 12 Siang CD" and r["REMARKS_DAY_MTS"]=="Same Day") else
                  "LM - Late FW to Sub Agen", axis=1
    )

    # REMARKS >5 C D
    df["REMARKS_>5_CD"] = df.apply(
        lambda r: None if pd.isna(r["REMARK_IM_5"]) else
                  "LM - IM Backdate" if r["REMARKS_DAY_MTS"]=="LM - IM Backdate" else
                  "Ontime Cabang Utama FW Sub Agen" if (r["REMARK_IM_5"]=="IM > 12 Siang CD" and r["REMARKS_DAY_MTS"] in ["Same Day","Next Day"]) else
                  "LM - Late FW to Sub Agen", axis=1
    )

    # FINAL MTS
    df["FINAL_MTS"] = np.where(df["REMARK_IM_5"]=="IM < 5 Sore", df["REMARKS_<5_CD"], df["REMARKS_>5_CD"])

    # FINAL SUBAGEN
    df["FINAL_FW_SUBAGEN"] = np.where(df["ZONA"].isin(["A","B"]), df["REMARKS_FINAL_ZONA_A"], df["FINAL_MTS"])

    # MTS_MTI
    df["MTS_MTI"] = (df["MANIFEST_INBOUND_SUBAGEN_DATE"] - df["MANIFEST_TRANSIT_SUBAGEN_DATE"]).apply(dur_days)

    # REMARK_MTS_MTI
    def remark_mts_mti(r):
        if pd.isna(r): return None
        if r<0: return "LM - MTI Backdate"
        if r>=2: return "LM - Late Inbound Sub Agen"
        return "Ontime Inbound Sub Agen"
    df["REMARK_MTS_MTI"] = df["MTS_MTI"].apply(remark_mts_mti)

    # FINAL FW MTI
    df["FINAL_FW_MTI"] = df.apply(
        lambda r: None if pd.isna(r["FINAL_FW_SUBAGEN"]) else
                  r["REMARK_MTS_MTI"] if r["FINAL_FW_SUBAGEN"]=="Ontime Cabang Utama FW Sub Agen" and pd.notna(r["REMARK_MTS_MTI"]) else
                  r["FINAL_FW_SUBAGEN"], axis=1
    )

    # JAM MTI
    df["JAM_MTI"] = pd.to_datetime(df["MANIFEST_INBOUND_SUBAGEN_DATE"], errors="coerce").dt.hour

    # REMARKS MTI 5
    df["REMARKS_MTI_5"] = df["JAM_MTI"].apply(
        lambda x: None if pd.isna(x) else "MTI < 5 Sore" if x==17 else "MTI > 5 Sore"
    )

    # 1St Runsheet_MTI
    df["1ST_RUNSHEET_MTI"] = (df["1ST_RUNSHEET_DATE"] - df["MANIFEST_INBOUND_SUBAGEN_DATE"]).apply(dur_days)

    # REMARKS DAY MTI
    def remark_day_mti(x):
        if pd.isna(x): return None
        if x==0: return "Same Day"
        if x==1: return "Next Day"
        if x<0: return "LM - MTI Backdate"
        return "Another Day"
    df["REMARKS_DAY_MTI"] = df["1ST_RUNSHEET_MTI"].apply(remark_day_mti)

    # REMARKS <5 MTI
    df["REMARKS_<5_MTI"] = df.apply(
        lambda r: None if pd.isna(r["REMARKS_MTI_5"]) else
                  "LM - MTI Backdate" if r["REMARKS_DAY_MTI"]=="LM - MTI Backdate" else
                  "LM - Ontime Runsheet Zona C D" if (r["REMARKS_MTI_5"]=="MTI < 5 Sore" and r["REMARKS_DAY_MTI"]=="Same Day") else
                  "LM - Late Runsheet Zona C D", axis=1
    )

    # REMARKS >5 MTI
    df["REMARKS_>5_MTI"] = df.apply(
        lambda r: None if pd.isna(r["REMARKS_MTI_5"]) else
                  "LM - MTI Backdate" if r["REMARKS_DAY_MTI"]=="LM - MTI Backdate" else
                  "LM - Ontime Runsheet Zona C D" if (r["REMARKS_MTI_5"]=="MTI > 5 Sore" and r["REMARKS_DAY_MTI"] in ["Same Day","Next Day"]) else
                  "LM - Late Runsheet Zona C D", axis=1
    )

    # FINAL MTI
    df["FINAL_MTI"] = np.where(df["REMARKS_MTI_5"]=="MTI < 5 Sore", df["REMARKS_<5_MTI"], df["REMARKS_>5_MTI"])

    # FINAL MTI SUBAGEN
    df["FINAL_MTI_SUBAGEN"] = np.where(
        df["FINAL_FW_MTI"].isna(), df["FINAL_MTI"],
        np.where(df["FINAL_FW_MTI"]=="Ontime Inbound Sub Agen", df["FINAL_MTI"], df["FINAL_FW_MTI"])
    )

    # FINAL COMBINE MTI SUBAGEN
    df["FINAL_COMBINE_MTI_SUBAGEN"] = np.where(
        df["FINAL_MTI_SUBAGEN"].isna() & (df["FINAL_FW_MTI"]=="Ontime Cabang Utama FW Sub Agen"),
        "Un MTI", df["FINAL_MTI_SUBAGEN"]
    )

    # FINAL COMBINE LASTMILE
    df["FINAL_COMBINE_LASTMILE"] = np.where(
        (df["FINAL_FW_MTI"]=="Ontime Cabang Utama FW Sub Agen") & (df["FINAL_MTI"].isna()),
        "LM - UnMTI", df["FINAL_COMBINE_MTI_SUBAGEN"]
    )

    # FM / MM / LM flags
    df["FM"] = df["REMARKS_ENTRY_OM"].apply(lambda x: None if pd.isna(x) or "Ontime" in str(x) else x)
    df["MM"] = df["REMARKS_MM"].apply(lambda x: None if pd.isna(x) or "Ontime" in str(x) else x)
    df["LM"] = df["FINAL_COMBINE_LASTMILE"].apply(lambda x: None if pd.isna(x) or "Late" not in str(x) else x)

    # Un-series
    df["Un Rcc"] = df["RECEIVING_DATE"].apply(lambda x: "FM - Unreceiving" if pd.isna(x) else None)
    df["Un Om"] = df["OM_HVO_HBG"].apply(lambda x: "FM - Un Manifest" if pd.isna(x) else None)
    df["Un Im"] = df["IM_HVI_PRA"].apply(lambda x: "MM - Un Inbound" if pd.isna(x) else None)
    df["Un MTS"] = df["MANIFEST_TRANSIT_SUBAGEN_DATE"].apply(lambda x: "LM - Un MTS" if pd.isna(x) else None)
    df["Un MTI"] = df["MANIFEST_INBOUND_SUBAGEN_DATE"].apply(lambda x: "LM - Un MTI" if pd.isna(x) else None)
    df["Un Runsheet"] = df["1ST_RUNSHEET_DATE"].apply(lambda x: "LM - Un Runsheet" if pd.isna(x) else None)

    # Aging Delivery Courier
    df["AGING_DELIVERY_COURIER"] = (df["TGL_RECEIVED"] - df["1ST_RUNSHEET_DATE"]).apply(dur_days)
    df["POD_COURIER"] = df["AGING_DELIVERY_COURIER"].apply(
        lambda x: None if pd.isna(x) else "LM - Late Update POD" if x > 1 else None
    )

    # FD Undel
    df["FD_UNDEL"] = df["DATE_LAST_ATTEMPT"].apply(lambda x: "LM - Late FD (Undel)" if pd.notna(x) else None)

    # Cancel
    df["CANCEL"] = df["STATUS_POD"].apply(
        lambda x: None if pd.isna(x) else "FM - AWB Cancel" if "Cancel" in str(x) else None
    )

    # FW
    df["FW"] = df["NO_CNOTE_FW"].apply(
        lambda x: None if pd.isna(x) else "LM - Problem Misroute" if "FW" in str(x) else None
    )

    # Cukai
    df["CUKAI"] = df["HOLD_REASON"].apply(
        lambda x: None if pd.isna(x) else "MM - Late Problem Bea Cukai"
        if ("Cukai" in str(x) or "Bea" in str(x)) else None
    )

    df["GABUNGAN_ANALISA"] = (
        df[["FM","MM","LM","Un Rcc","Un Om","Un Im","Un MTS","Un MTI",
            "Un Runsheet","CARRER_POD","POD_COURIER","FD_UNDEL","CANCEL","FW","CUKAI"]]
        .applymap(safe_str)
        .agg(", ".join, axis=1)
    )

    # Flagging FM/MM/LM late
    df["FM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "FM - LATE" if "FM" in x else None
    )
    df["MM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "MM - LATE" if "MM" in x else None
    )
    df["LM_LATE"] = df["GABUNGAN_ANALISA"].apply(
        lambda x: "LM - LATE" if "LM" in x else None
    )

    df["GROUPING_LATE"] = (
        df[["FM_LATE", "MM_LATE", "LM_LATE"]]
        .apply(lambda row: ", ".join([v for v in row if v not in [None, ""]]), axis=1)
    )

    # Hanya isi kalau CARRER_POD == "Over SLA", selain itu kosong
    df.loc[~df["CARRER"].isin(["Over SLA", "Over SLA (became)"]), "GROUPING_LATE"] = ""
    df.loc[(df["CARRER"] == "Over SLA") & (df["GROUPING_LATE"].isna() | (df["GROUPING_LATE"] == "")), "GROUPING_LATE"] = "LM - LATE"
    
    # Bersihkan kolom bantu
    df = df.drop(
        columns=[
            # gabungan akhir
            "GABUNGAN_ANALISA","FM_LATE","MM_LATE","LM_LATE",
            # remark FM/MM/LM awal
            "FM","MM","LM","Un Rcc","Un Om","Un Im","Un MTS","Un MTI","Un Runsheet",
            "CARRER_POD","POD_COURIER","FD_UNDEL","CANCEL","FW","CUKAI",
            "FINAL_COMBINE_LASTMILE","FINAL_COMBINE_MTI_SUBAGEN","FINAL_MTI_SUBAGEN",
            "FINAL_MTI","REMARKS_>5_MTI","REMARKS_<5_MTI","REMARKS_DAY_MTI",
            "1ST_RUNSHEET_MTI","REMARKS_MTI_5","JAM_MTI","FINAL_FW_MTI",
            "REMARK_MTS_MTI","MTS_MTI","FINAL_FW_SUBAGEN",
            "POD_COURIER","AGING_DELIVERY_COURIER",
            # blok ORG_DEST sampai FINAL_MTS
            "ORG_DEST","OM_HVO","OM_HVO_HBG","PU_ENTRY","ENTRY_TO_OM","PU_HBG",
            "REMARKS_ENTRY_OM","REMARKS_PU_HBG",
            "IM_HVI","IM_HVI_PRA","HBG_IM","CHECK_LT_MM_ETD","CHECK_LT_MM",
            "REMARKS_MM","JAM_IM","REMARK_IM_5_1","REMARK_IM_5_2","REMARK_IM_5",
            "1ST_RUNSHEET_HVI","REMARKS_DAY","REMARKS_<5_A","REMARKS_>5_A",
            "REMARKS_FINAL_ZONA_A","MTS_IM","REMARKS_DAY_MTS",
            "REMARKS_<5_CD","REMARKS_>5_CD","FINAL_MTS"
        ],
        errors="ignore"
    )

    return df

def add_PIC(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["PIC"] = "JNE"  

    return df

def add_SBS(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "ORIGIN" not in df.columns:
        print("⚠️ Kolom 'ORIGIN' tidak ditemukan, tidak bisa menambahkan ORIGIN 2.")
        return df

    df["ORIGIN 2"] = np.where(
        df["ORIGIN"].astype(str).str.startswith("CGK"),
        "Non SBS",
        "SBS"
    )
    return df

def add_grouping_sla(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "SLA" not in df.columns:
        print("⚠️ Kolom 'SLA' tidak ditemukan, tidak bisa menambahkan GROUPING_SLA.")
        return df

    def categorize_sla(val):
        try:
            v = int(val)
        except (ValueError, TypeError):
            return ""
        
        if v == 0:
            return "TODAY"
        elif v == -1:
            return "H-1"
        elif v == -2:
            return "H-2"
        elif v == -3:
            return "H-3"
        elif v <= -4:
            return ">H-4"
        elif v >= 1:
            return "OVER SLA"
        else:
            return ""

    df["GROUPING_SLA"] = df["SLA"].apply(categorize_sla)
    return df

def add_date_time_received(df: pd.DataFrame, debug=False) -> pd.DataFrame:
    df = df.copy()
    for col in ["DATE_RECEIVED", "TIME_RECEIVED"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if "TGL_RECEIVED" not in df.columns:
        print("❌ Kolom 'TGL_RECEIVED' tidak ditemukan.")
        return df

    # Pastikan TGL_RECEIVED datetime
    if not pd.api.types.is_datetime64_any_dtype(df["TGL_RECEIVED"]):
        if debug:
            print("add_date_time_received: TGL_RECEIVED belum datetime, mencoba konversi fallback...")
        df["TGL_RECEIVED"] = pd.to_datetime(df["TGL_RECEIVED"], errors="coerce")

    # Buat kolom
    df["DATE_RECEIVED"] = df["TGL_RECEIVED"].dt.date
    df["TIME_RECEIVED"] = df["TGL_RECEIVED"].dt.strftime("%H:%M:%S")

    if debug:
        print(f"add_time_received: sample: {df[['TGL_RECEIVED','DATE_RECEIVED','TIME_RECEIVED']].head(3).to_dict('records')}")

    return df

def add_dept_PZC(df: pd.DataFrame, debug=False) -> pd.DataFrame:
    df = df.copy()
    df = add_cust_name(df)
    for col in ["DEPT PZC"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if "ID_ACCOUNT" not in df.columns or "CUST_NAME" not in df.columns:
        print("❌ Kolom 'ID_ACCOUNT' atau 'CUST_NAME' tidak ditemukan.")
        return df

    # Buat kolom
    df["DEPT PZC"] = df["ID_ACCOUNT"].astype(str) + " " + df["CUST_NAME"].astype(str)

    if debug:
        print(f"add_dept_PZC: sample: {df[['ID_ACCOUNT','CUST_NAME','DEPT PZC']].head(3).to_dict('records')}")

    return df

def add_AJCar_status(
    df: pd.DataFrame,
    ref=_TABLE_REF,
    debug=False
) -> pd.DataFrame:
    df = df.copy()
    df = add_status_pod(df)
    
    # hapus AJCar_Status lama kalau ada
    for col in ["AJCar_Status"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    if "STATUS_POD" not in df.columns:
        print("❌ Kolom 'STATUS_POD' tidak ditemukan.")
        return df
    
    # Buat kolom
    try:
        ref_df = pd.read_excel(ref, sheet_name="posisisis")
        required = {"Status", "Grouping BIG Status", "AJ Car Status", "KETERANGAN AJ CAR"}
        if not required.issubset(ref_df.columns):
            print("⚠️ Sheet posisisis tidak memiliki kolom Status, Grouping BIG Status, AJ Car Status atau KETERANGAN AJ CAR.")
            return df

        df = df.merge(
            ref_df[["Status", "Grouping BIG Status", "AJ Car Status", "KETERANGAN AJ CAR"]],
            how="left",
            left_on="STATUS_POD",
            right_on="Status"
        )

        # drop Status join key
        df.drop(columns=["Status"], inplace=True)

        # rename Grouping BIG Status → Remark Return
        df.rename(columns={"Grouping BIG Status": "Remark Return"}, inplace=True)

    except Exception as e:
        print(f"⚠️ Gagal menambahkan kolom AJ Car Status & KETERANGAN AJ CAR: {e}")

    return df

def add_is_close(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "STATUS_POD" not in df.columns:
        print("⚠️ Kolom 'STATUS_POD' tidak ditemukan, tidak bisa menambahkan IS_CLOSE.")
        return df

    # Tambah kolom IS_CLOSE (string "x" atau kosong)
    df["IS_CLOSE"] = df["STATUS_POD"].apply(
        lambda x: "x" if str(x).strip().lower() in ["success", "return shipper"] else ""
    )

    # Urutkan: yang IS_CLOSE = "x" dulu, lalu STATUS_POD A-Z
    df = df.sort_values(by=["IS_CLOSE", "STATUS_POD"], ascending=[False, True]).reset_index(drop=True)

    return df

def add_reason(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "STATUS_POD" not in df.columns:
        print("⚠️ Kolom 'STATUS_POD' tidak ditemukan, tidak bisa menambahkan REASON.")
        return df

    # pastikan kolom AGING_POD ada, kalau tidak isi dengan None
    if "AGING_POD" not in df.columns:
        df["AGING_POD"] = None

    def map_reason(row):
        if row["STATUS_POD"] == "Success":
            return "CLOSE CABANG"
        elif row["STATUS_POD"] == "Return Shipper":
            return "CLOSE CABANG"
        elif row["STATUS_POD"] != "Success":
            return "ON FOLLOW UP CABANG"
        elif row["AGING_POD"] != "Return Shipper":
            return "ON FOLLOW UP CABANG"
        else:
            return None

    df["REASON"] = df.apply(map_reason, axis=1)
    return df

def add_no(df: pd.DataFrame, debug: bool = False) -> pd.DataFrame:
    """
    Adds a 'NO' column with sequential numbers (1, 2, 3, ...) to the DataFrame.
    If DataFrame is empty, adds an empty 'NO' column.
    
    Args:
        df (pd.DataFrame): Input DataFrame.
        debug (bool): If True, prints debug information.
    
    Returns:
        pd.DataFrame: DataFrame with 'NO' column added at index 0.
    """
    df = df.copy()
    if debug:
        print(f"[DEBUG] add_no: DataFrame size: {len(df)} rows")
    if "NO" in df.columns:
        df = df.drop(columns=["NO"])
        if debug:
            print("[DEBUG] add_no: Dropped existing 'NO' column")
    if len(df) > 0:
        df.insert(0, "NO", range(1, len(df) + 1))
        if debug:
            print(f"[DEBUG] add_no: Added 'NO' column with values 1..{len(df)}")
            print(f"[DEBUG] add_no: First few 'NO' values: {df['NO'].head().tolist()}")
    else:
        df.insert(0, "NO", [])
        if debug:
            print("[DEBUG] add_no: DataFrame empty, added empty 'NO' column")
    return df

def add_reason_last_attempt(df: pd.DataFrame, ref_path: str, sheet_name: str = "Coding Undel") -> pd.DataFrame:
    df = df.copy()

    if "RESULT_LAST_ATTEMPT" not in df.columns:
        print("⚠️ Kolom 'RESULT_LAST_ATTEMPT' tidak ditemukan, tidak bisa menambahkan REASON_LAST_ATTEMPT.")
        return df

    # Load referensi
    ref = pd.read_excel(ref_path, sheet_name=sheet_name)

    # Pastikan ada kolom kunci di ref
    if "Coding Undel" not in ref.columns or "Remark_Bahasa" not in ref.columns:
        print("⚠️ Sheet referensi tidak punya kolom 'Coding Undel' dan/atau 'Remark_Bahasa'")
        return df

    # Ambil hanya kolom kunci + Remark_Bahasa
    ref = ref[["Coding Undel", "Remark_Bahasa"]].drop_duplicates()

    # Join df.RESULT_LAST_ATTEMPT dengan ref["Coding Undel"]
    df = df.merge(ref, left_on="RESULT_LAST_ATTEMPT", right_on="Coding Undel", how="left", validate="m:1")

    # Drop kolom kunci dari ref biar tidak duplikat
    df = df.drop(columns=["Coding Undel"])

    # Rename Remark_Bahasa → REASON_LAST_ATTEMPT
    df = df.rename(columns={"Remark_Bahasa": "REASON_LAST_ATTEMPT"})

    return df

def add_date_receive_request(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def compute_value(row):
        val = row.get("CONNOTE_RETURN_RT")
        if pd.isna(val):
            return row.get("TGL_RECEIVED")
        elif isinstance(val, str) and "RT" in val:
            return row.get("DATE_CONNOTE_RETURN_RT")
        else:
            return None

    df["DATE_RECEIVE_REQUEST"] = df.apply(compute_value, axis=1)

    return df

def add_descr_return(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tambahkan kolom DESCR_RETURN:
    - "RETURN SICEPAT" jika STATUS_POD == "Return Shipper"
    - else kosong (None)
    """
    df = df.copy()
    if "STATUS_POD" not in df.columns:
        print("⚠️ Kolom 'STATUS_POD' tidak ditemukan, tidak bisa menambahkan DESCR_RETURN.")
        return df

    df["DESCR_RETURN"] = np.where(
        df["STATUS_POD"].astype(str).str.strip().eq("Return Shipper"),
        "RETURN SICEPAT",
        None
    )

    return df

def add_update_time(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tambahkan kolom UPDATE_TIME:
    - Isi dengan tanggal hari ini (tanpa jam).
    """
    df = df.copy()
    today = datetime.datetime.today().date()  # hanya tanggal
    df["UPDATE_TIME"] = today
    return df

def load_uob_pickup_data(bulan: int, tahun: int) -> pd.DataFrame:
    """
    Baca semua file .txt pickup UOB untuk bulan+tahun tertentu.
    Return DataFrame dengan kolom:
    REFF NUM, CUSTOMER NAME, PICK UP DATE, CYCLE NAME, 
    NAMA FILE PICK UP, CYCLE & JENIS
    """
    all_rows = []
    UOB_BASE = r"D:\RYAN\Python Scripts\Bot Report Gabungan\data\uob"

    bulan_id = {
        1: "JANUARI", 2: "FEBRUARI", 3: "MARET", 4: "APRIL",
        5: "MEI", 6: "JUNI", 7: "JULI", 8: "AGUSTUS",
        9: "SEPTEMBER", 10: "OKTOBER", 11: "NOVEMBER", 12: "DESEMBER"
    }
    bulan_str = bulan_id[bulan].upper()

    # mapping kategori → singkatan
    CATEGORY_MAP = {
        "BS CREDIT CARD": "BSCC",
        "BS UOB CASHPLUS": "BSCP",
    }

    # cari folder sesuai bulan & tahun
    pattern = os.path.join(UOB_BASE, f"*{bulan_str} {tahun}*")
    folders = glob.glob(pattern)

    for folder in folders:
        folder_name = os.path.basename(folder)

        # --- cari cycle name ---
        match = re.search(r"(CY\s[0-9\-]+\s+[A-Z]+\s+\d{4})", folder_name.upper())
        cycle_name = match.group(1).upper() if match else ""

        # --- ambil kategori ---
        kategori_raw = folder_name.upper().replace("DATA ", "").split("CY")[0].strip()
        kategori_singkat = CATEGORY_MAP.get(kategori_raw, kategori_raw.replace(" ", ""))

        # --- buat base nama file ---
        nama_file_base = f"JNE_{kategori_singkat} {cycle_name.rsplit(' ', 1)[0]}"

        for txt_file in glob.glob(os.path.join(folder, "*.txt")):
            txt_name = os.path.basename(txt_file).upper()

            if "SD_OUTPUT" in txt_name:
                nama_file_pickup = f"{nama_file_base}_SD.txt"
            else:
                nama_file_pickup = f"{nama_file_base}.txt"

            # --- tentukan CYCLE & JENIS ---
            jenis_prefix = "BS" if kategori_singkat == "BSCC" else "CP"
            # --- buang bulan & tahun dari cycle_name ---
            cycle_part = re.sub(r"\s+[A-Z]+\s+\d{4}$", "", cycle_name).strip()

            if nama_file_pickup.endswith("_SD.txt"):
                cycle_jenis = f"{jenis_prefix} {cycle_part} STAMP"
            else:
                cycle_jenis = f"{jenis_prefix} {cycle_part} NON STAMP"

            with open(txt_file, "r", encoding="latin-1") as f:
                lines = f.readlines()

            for line in lines[1:]:
                parts = [p.strip() for p in line.split(";") if p.strip()]
                if len(parts) < 3:
                    continue

                all_rows.append({
                    "REFF NUM": parts[0],
                    "CUSTOMER NAME": parts[1],
                    "PICK UP DATE": parts[-1],
                    "CYCLE NAME": cycle_name,
                    "NAMA FILE PICK UP": nama_file_pickup,
                    "CYCLE & JENIS": cycle_jenis,
                    "PERIODE UOB": bulan_str
                })

    if not all_rows:
        return pd.DataFrame(columns=[
            "REFF NUM", "CUSTOMER NAME", "PICK UP DATE", 
            "CYCLE NAME", "NAMA FILE PICK UP", "CYCLE & JENIS"
        ])

    df_uob = pd.DataFrame(all_rows)

    # normalisasi tanggal
    df_uob["PICK UP DATE"] = pd.to_datetime(
        df_uob["PICK UP DATE"], errors="coerce", format="%m/%d/%Y"
    )

    # --- tambah kolom JENIS BILLING ---
    def get_jenis_billing(nama_file: str) -> str:
        nama_file = str(nama_file).upper()
        if "BSCC" in nama_file:
            return "BS STAMP" if "_SD" in nama_file else "BS NON STAMP"
        elif "BSCP" in nama_file:
            return "BSCP STAMP" if "_SD" in nama_file else "BSCP NON STAMP"
        return pd.NA

    df_uob["JENIS BILLING"] = df_uob["NAMA FILE PICK UP"].map(get_jenis_billing)

    return df_uob


def add_uob_pickup_data_cols(df: pd.DataFrame, bulan_ke_belakang: int = 2) -> pd.DataFrame:
    """
    Join pickup UOB data ke df berdasarkan REFNO_UOB = REFF NUM.
    Tambah kolom: 
      - REFF NUM
      - CUSTOMER NAME
      - PICK UP DATE
      - CYCLE NAME
      - RECIPIENT
      - RECIPIENT RELATION (hanya untuk STATUS UOB = 'S')
      - STATUS UOB
      - REASON 2 (hanya untuk STATUS UOB = 'R')
      - REMARK (AWB jika STATUS UOB = 'S', atau AWB + REASON RETURN jika STATUS UOB = 'R')
    """
    today = datetime.datetime.today()
    all_uob = []

    # --- load data UOB beberapa bulan terakhir ---
    for i in range(bulan_ke_belakang):
        target_date = today - relativedelta(months=i)
        bulan = target_date.month
        tahun = target_date.year
        df_uob = load_uob_pickup_data(bulan, tahun)
        if not df_uob.empty:
            all_uob.append(df_uob)

    if not all_uob:
        print(f"⚠️ Tidak ada data UOB ditemukan {bulan_ke_belakang} bulan terakhir")
        for col in [
            "REFF NUM", "CUSTOMER NAME", "PICK UP DATE", "CYCLE NAME",
            "RECIPIENT", "RECIPIENT RELATION", "STATUS UOB", "REASON 2", "REMARK"
        ]:
            df[col] = pd.NA
        return df

    df_uob_all = (
        pd.concat(all_uob, ignore_index=True)
        .drop_duplicates(subset=["REFF NUM"])
        .copy()
    )

    # --- normalisasi key ---
    if "REFNO_UOB" not in df.columns:
        print("⚠️ Kolom REFNO_UOB tidak ditemukan di dataframe utama")
        df["REFNO_UOB"] = ""

    df["REFNO_UOB"] = (
        df["REFNO_UOB"].astype(str)
        .str.strip()
        .str.replace("'", "", regex=False)
        .str.replace("*", "", regex=False)
    )
    df_uob_all["REFF NUM"] = df_uob_all["REFF NUM"].astype(str).str.strip()

    # --- merge ---
    df_merged = df.merge(
        df_uob_all,
        how="right",
        left_on="REFNO_UOB",
        right_on="REFF NUM"
    )

    if "REFF NUM_x" in df_merged.columns:
        df_merged.drop(columns=["REFF NUM_x"], inplace=True, errors="ignore")
        df_merged.rename(columns={"REFF NUM_y": "REFF NUM"}, inplace=True)

    # --- kolom RECIPIENT ---
    if {"STATUS_POD", "RECEIVED/REASON"}.issubset(df_merged.columns):
        df_merged["RECIPIENT"] = np.where(
            df_merged["STATUS_POD"].astype(str).str.lower().eq("success"),
            df_merged["RECEIVED/REASON"],
            pd.NA
        )
    else:
        df_merged["RECIPIENT"] = pd.NA

    # --- mapping CODING ---
    coding_map = {
        "CR1": ("Retur Origin", "R"),
        "D01": ("01", "S"),
        "D02": ("07", "S"),
        "D03": ("08", "S"),
        "D04": ("06", "S"),
        "D05": ("08", "S"),
        "D06": ("02", "S"),
        "D07": ("05", "S"),
        "D08": ("08", "S"),
        "D09": ("03", "S"),
        "D10": ("10", "S"),
        "D11": ("08", "S"),
        "D12": ("08", "S"),
        "DB1": ("08", "S"),
        "DB2": ("08", "S"),
        "R10": ("06", "R"),
        "U01": ("03", "R"),
        "U02": ("04", "R"),
        "U03": ("02", "R"),
        "U04": ("06", "R"),
        "U05": ("07", "R"),
        "U06": ("08", "R"),
        "U07": ("07", "R"),
        "U08": ("08", "R"),
        "U09": ("07", "R"),
        "U10": ("10", "R"),
        "U11": ("06", "R"),
        "U12": ("06", "R"),
        "U13": ("08", "R"),
        "U14": ("07", "R"),
        "U21": ("08", "R"),
        "U22": ("07", "R"),
        "U23": ("07", "R"),
        "U24": ("06", "R"),
        "U25": ("07", "R"),
        "OPC": ("On Process", "On Process"),
    }

    df_merged["CODING"] = df_merged["CODING"].astype(str)
    
    # --- perbaikan untuk coding kosong ---
    df_merged["CODING"] = (
        df_merged["CODING"]
        .fillna("OPC")             # isi NaN dengan OPC
        .replace(r"^\s*$", "OPC", regex=True)  # isi string kosong / spasi jadi OPC
    )

    # hasil mapping
    df_merged["MAP_RELATION"] = df_merged["CODING"].map(lambda x: coding_map.get(x, (pd.NA, pd.NA))[0])
    df_merged["STATUS UOB"] = df_merged["CODING"].map(lambda x: coding_map.get(x, (pd.NA, pd.NA))[1])

    # isi RECIPIENT RELATION hanya untuk STATUS UOB = "S"
    df_merged["RECIPIENT RELATION"] = np.where(
        df_merged["STATUS UOB"] == "S",
        df_merged["MAP_RELATION"],
        pd.NA
    )

    # isi REASON 2 hanya untuk STATUS UOB = "R"
    df_merged["REASON 2"] = np.where(
        df_merged["STATUS UOB"] == "R",
        df_merged["MAP_RELATION"],
        pd.NA
    )

    # --- isi kolom REMARK ---
    df_merged["REMARK"] = np.where(
        df_merged["STATUS UOB"] == "S",
        df_merged["AWB"],
        np.where(
            df_merged["STATUS UOB"] == "R",
            df_merged["AWB"].astype(str) + " " + df_merged["REASON RETURN"].astype(str),
            pd.NA
        )
    )

    # --- isi kolom COURIER ID
    df_merged["COURIER ID"] = "26"

    # --- isi kolom UPLOAD DATE
    df_merged["UPLOAD DATE"] = df_merged["PICK UP DATE"]

    # --- isi kolom KET AWB ---
    df_merged["KET AWB"] = np.where(
        df_merged["AWB"].notna() & df_merged["AWB"].astype(str).str.strip().ne(""),
        "ADA AWB",
        "TIDAK ADA AWB"
    )

    # --- isi kolom CLOSING DATE ---
    df_merged["CLOSING DATE"] = df_merged["TGL_RECEIVED"]

    # --- isi kolom STATUS CLOSING ---
    df_merged["STATUS CLOSING"] = np.where(
        df_merged["CLOSING DATE"].notna() & df_merged["CLOSING DATE"].astype(str).str.strip().ne(""),
        "SUDAH CLOSING",
        "BELUM CLOSING"
    )

    # drop kolom intermediate
    df_merged.drop(columns=["MAP_RELATION"], inplace=True)

    return df_merged

def add_sociolla_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Tambah kolom DIM dll
    df["DIM"] = 0
    df["DIM - Copy"] = df["DIM"]

    return df

def add_grouping_status(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "STATUS_POD" not in df.columns:
        print("⚠️ Kolom 'STATUS_POD' tidak ditemukan, tidak bisa menambahkan GROUPING_STATUS.")
        return df

    def categorize_status_pod(val):
        try:
            v = str(val).strip()
        except (ValueError, TypeError):
            return ""
        
        if v == "Success":
            return "Success"
        elif v in ["Return Shipper", "RU Origin"]:
            return "Process Return"
        elif v in ["Missing", "AWB Cancel", "Hold WH Destination", "Undel"]:
            return "Undel"
        else:
            return "On Process"

    df["GROUPING_STATUS"] = df["STATUS_POD"].apply(categorize_status_pod)
    return df

def fix_regional_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Revisi semua kolom yang mengandung kata 'REGIONAL'
    - Replace 'Sumatara' (case-insensitive) menjadi 'Sumatera'
    """
    for col in df.columns:
        if "REGIONAL" in col.upper():
            # Pastikan kolom bertipe string
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"(?i)sumatara", "Sumatera", regex=True)
            )
    return df

# ------------------- MAPPING KOMPONEN TRANSFORMASI -------------------
TRANSFORM_GROUPS = {
    # Group yang hasilkan banyak kolom sekaligus
    "ORIGIN_CITY_GROUP": {
        "cols": ["ORIGIN_CITY", "ORIGIN_CITY_2", "3_LC_ORIGIN", "PROVINSI_ORIGIN", "REGION", "KODE_POS_ORI"],
        "func": lambda df, ref: add_origin_city(df, ref),
    },
    "PROVINCE_GROUP": {
        "cols": ["KECAMATAN", "NAMA KAB/KOTA", "PROVINSI", "KODE_POS"],
        "func": lambda df, ref: add_province_zipcode(df, ref),
    },
    "PERIODE_GROUP": {
        "cols": ["DAY", "PERIODE", "WEEK", "WEEK_OF_YEAR"],
        "func": lambda df, ref: add_periode(df),
    },
    "TGL_RECEIVED_GROUP": {
        "cols": ["DATE_RECEIVED", "TIME_RECEIVED"],
        "func": lambda df, ref: add_date_time_received(df),
    },
    "AJ_CAR_GROUP": {
        "cols": ["AJ Car Status", "KETERANGAN AJ CAR", "Remark Return"],
        "func": lambda df, ref: add_AJCar_status(df, ref),
    },
    "UOB_GROUP": {
        "cols": ["CYCLE NAME", "REFF NUM", "PICK UP DATE", "CUSTOMER NAME", "RECIPIENT RELATION", "STATUS UOB", "REASON 2"],
        "func": lambda df, ref: add_uob_pickup_data_cols(df)
    },
    "SOCIOLLA_GROUP": {
        "cols": ["DIM", "DIM - Copy"],
        "func": lambda df, ref: add_sociolla_cols(df)
    }
}

# Fungsi yang hanya hasilkan satu kolom
TRANSFORM_FUNCS = {
    "ADDRESS": lambda df, ref: add_address(df, "ADDRESS"),
    "STATUS_POD": lambda df, ref: add_status_pod(df),
    "CUST_NAME": lambda df, ref: add_cust_name(df),
    "STATUS_LATLONG": lambda df, ref: add_status_latlong(df),
    "ETA": lambda df, ref: add_eta(df),
    "CODING_DELIVERY": lambda df, ref: add_coding_delivery(df, ref),
    "GROUPING_LATE": lambda df, ref: add_grouping_late(df),
    "GROUPING_SLA": lambda df, ref: add_grouping_sla(df),
    "PIC": lambda df, ref: add_PIC(df),
    "ORIGIN 2": lambda df, ref: add_SBS(df),
    "DEPT PZC": lambda df, ref: add_dept_PZC(df),
    "IS_CLOSE": lambda df, ref: add_is_close(df),
    "REASON": lambda df, ref: add_reason(df),
    "REASON_LAST_ATTEMPT": lambda df, ref: add_reason_last_attempt(df, ref),
    "DATE_RECEIVE_REQUEST": lambda df, ref: add_date_receive_request(df),
    "DESCR_RETURN": lambda df, ref: add_descr_return(df),
    "UPDATE_TIME": lambda df, ref: add_update_time(df),
    "GROUPING_STATUS": lambda df, ref: add_grouping_status(df)
}